"""Classifica os scriptlets dos pacotes que saíram do rpmdb no build e grava ``build-removed-scriptlets.json``.

Roda no Execution Environment, depois do Packer, sobre o pacote que o guest devolveu (``build-scriptlets.tar.gz``):
``scriptlets.txt`` (scriptlets de todo pacote instalado antes do Ansible, por NEVRA), ``history-before.sqlite`` (histórico
do dnf no mesmo instante; ``history-before.absent`` sem histórico na base) e ``history.sqlite`` (no fim do build). Só as
transações posteriores ao snapshot são do build; o que saiu antes vai em ``left_before_build``. O classificador é o da sonda de B (``inventory_collect/files/collect_rhel_package_usage.py``):
mesma leitura de scriptlet, mesma regra de spool. O artefato diz, por NEVRA que saiu, se os scriptlets foram medidos
e que spools eles podem escrever. A decisão fica na N5 (``shared.build_removed_scriptlets.removed_state``).

Compatível com Python 3.6. Uso: ``build_removed_scriptlets.py --archive A --probe P --output O``.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tarfile
import tempfile

SCHEMA = "samurai.build_removed_scriptlets/v1"
SNAPSHOT_MEMBER = "scriptlets.txt"
HISTORY_MEMBER = "history.sqlite"
HISTORY_BEFORE_MEMBER = "history-before.sqlite"
HISTORY_BEFORE_ABSENT = "history-before.absent"
MEMBERS = (SNAPSHOT_MEMBER, HISTORY_MEMBER, HISTORY_BEFORE_MEMBER, HISTORY_BEFORE_ABSENT)
SPOOL_KEYS = ("at_jobs", "user_crontab_spool")
MAX_MEMBER_BYTES = 268435456


def load_probe(path):
    """A sonda de B como módulo, e a identidade dela pela mesma regra do recibo (``probe_sha256``)."""
    spec = importlib.util.spec_from_file_location("samurai_package_usage_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.probe_sha256(file_name=path)


def read_members(archive):
    """Os arquivos esperados do pacote, pelo nome; o resto é ignorado."""
    members = {}
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for info in tar.getmembers():
                name = info.name.rsplit("/", 1)[-1]
                if not info.isfile() or name not in MEMBERS or name in members:
                    continue
                if info.size > MAX_MEMBER_BYTES:
                    continue
                members[name] = tar.extractfile(info).read()
    except (OSError, tarfile.TarError, EOFError):
        return {}
    return members


def nevra_key(nevra):
    """``nome-[E:]V-R.A`` para ``(nome, época, versão, release, arquitetura)``; época ausente vale 0."""
    rest, _, arch = nevra.rpartition(".")
    rest, _, release = rest.rpartition("-")
    name, _, version = rest.rpartition("-")
    epoch, separator, plain = version.partition(":")
    if not separator:
        epoch, plain = "0", version
    return name, epoch or "0", plain, release, arch


def nevra_text(key):
    name, epoch, version, release, arch = key
    shown = "" if epoch == "0" else epoch + ":"
    return f"{name}-{shown}{version}-{release}.{arch}"


SNAPSHOT_END = "@@samurai-snapshot-end"


#: Marcador de fim do snapshot, com o rc do ``rpm -qa``: o leitor só aceita o snapshot que termina com ``rc=0``.
def snapshot_complete(text):
    """Snapshot completo (R10-3): a última linha não vazia é o marcador de fim com ``rc=0``."""
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == SNAPSHOT_END + " rc=0"


def read_snapshot(probe, text):
    """Linhas de scriptlet por NEVRA e os spools que cada NEVRA pode escrever, pelo classificador da sonda."""
    lines = [line for line in text.splitlines() if not line.startswith(SNAPSHOT_END)]
    blocks, current = {}, None
    for line in lines:
        if line.startswith(probe.SCRIPTLET_MARK):
            current = line[len(probe.SCRIPTLET_MARK):].strip()
            blocks.setdefault(current, [])
        elif current is not None:
            blocks[current].append(line)

    def replay(argv, on_line):
        for line in lines:
            on_line(line)
        return {"rc": 0, "truncated": False}

    measured = probe._scriptlets(replay)
    return blocks, measured


def last_transaction(members):
    """Última transação do histórico no snapshot: 0 quando a base não tinha histórico; ``None`` sem leitura."""
    if HISTORY_BEFORE_MEMBER not in members:
        return 0 if HISTORY_BEFORE_ABSENT in members else None
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, HISTORY_BEFORE_MEMBER)
        with open(path, "wb") as handle:
            handle.write(members[HISTORY_BEFORE_MEMBER])
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                value = connection.execute("SELECT max(id) FROM trans").fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error:
            return None
    return int(value or 0)


def read_history(probe, data, baseline=0):
    """Transações do histórico (pela leitura da sonda), cada NEVRA que saiu do rpmdb depois de ``baseline`` e os
    nomes que saíram até ``baseline`` (imagem base)."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, HISTORY_MEMBER)
        with open(path, "wb") as handle:
            handle.write(data)
        summary = probe.read_package_history(path)
        if summary.get("transactions", 0) <= 0:
            return 0, None, None
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        actions = ",".join(str(int(action)) for action in probe.LEFT_RPMDB_ACTIONS)
        try:
            rows = connection.execute(
                "SELECT DISTINCT trans_item.trans_id, rpm.name, rpm.epoch, rpm.version, rpm.release, rpm.arch "
                "FROM trans_item JOIN rpm ON rpm.item_id = trans_item.item_id "
                f"WHERE trans_item.action IN ({actions})").fetchall()
        except sqlite3.Error:
            return 0, None, None
        finally:
            connection.close()
    left = sorted({(str(name), str(epoch or 0), str(version), str(release), str(arch))
                   for trans, name, epoch, version, release, arch in rows if int(trans) > baseline})
    before = sorted({str(name) for trans, name, _, _, _, _ in rows if int(trans) <= baseline})
    return summary["transactions"], left, before


def build_document(probe, classifier_sha256, members):
    reasons = []
    blocks, measured = {}, {"packages": 0, "spool_writers": {}}
    if SNAPSHOT_MEMBER in members:
        text = members[SNAPSHOT_MEMBER].decode("utf-8", "replace")
        if snapshot_complete(text):
            blocks, measured = read_snapshot(probe, text)
        else:
            reasons.append("snapshot_incomplete")  # sem marcador de fim com rc 0: o removido não tem medição
    if not measured.get("packages") and "snapshot_incomplete" not in reasons:
        reasons.append("snapshot_unread")
    baseline = last_transaction(members)
    if baseline is None:
        reasons.append("history_before_unread")
    transactions, left, before = 0, None, None
    if HISTORY_MEMBER in members:
        transactions, left, before = read_history(probe, members[HISTORY_MEMBER], baseline or 0)
    if left is None:
        reasons.append("history_unread")
        left, before = [], []
    by_key = {nevra_key(nevra): nevra for nevra in blocks}
    writers = measured.get("spool_writers") or {}
    removed = []
    for key in left:
        measured_nevra = by_key.get(key)
        lines = blocks.get(measured_nevra, []) if measured_nevra is not None else []
        removed.append({
            "nevra": nevra_text(key),
            "name": key[0],
            "covered": measured_nevra is not None,
            "spools": [spool for spool in SPOOL_KEYS if measured_nevra in (writers.get(spool) or ())],
            "scriptlets_sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
            "scriptlets": "\n".join(lines),
        })
    if any(not item["covered"] for item in removed):
        reasons.append("removed_not_measured")
    return {"schema": SCHEMA, "classifier_sha256": classifier_sha256, "complete": not reasons,
            "reasons": reasons, "transactions": transactions, "snapshot_packages": measured.get("packages", 0),
            "baseline_transactions": baseline, "left_before_build": before, "removed": removed}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--expected-probe-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    probe, classifier_sha256 = load_probe(args.probe)
    if classifier_sha256 != args.expected_probe_sha256:
        parser.error("scriptlet classifier probe hash does not match the build pin")
    document = build_document(probe, classifier_sha256, read_members(args.archive))
    with open(args.output, "w") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
