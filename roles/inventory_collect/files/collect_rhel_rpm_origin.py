"""Junta a origem RPM e o rótulo de módulo do ``rpm -qa`` às entradas de ``packages``.

Só leitura: não toca no host. Recebe o inventário que o role já coletou e a saída
da consulta que o role já fez, e devolve as entradas com ``source_rpm`` e, quando o
banco expõe a tag, ``modularity_label``. Compatível com Python 2.7 e 3 porque roda
no interpretador que o host tiver (RHEL 7 usa 2.7 por padrão).

Regras:
* ``(none)`` em SOURCERPM não grava ``source_rpm`` (ex.: ``gpg-pubkey``).
* ``(none)`` em MODULARITYLABEL grava ``modularity_label`` vazio: pacote não modular.
* Sem a tag de módulo (RHEL 7), o rótulo não é gravado; só ``source_rpm`` entra.
* Casa por (nome, versão, release, arquitetura). Chave com valores diferentes no
  próprio banco não recebe campo: ambiguidade não vira valor inventado.
* Falha da consulta devolve as entradas intactas e declara a observação indisponível.
"""
import json
import sys

NONE = "(none)"
REASON_QUERY_FAILED = "rpm_origin_query_failed"


def _clean(raw):
    value = raw.strip()
    return "" if value == NONE else value


def _index_rpm_lines(stdout, with_modularity):
    """Mapa (nome, versão, release, arch) -> conjunto de (source_rpm, rótulo)."""
    width = 6 if with_modularity else 5
    index = {}
    malformed = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != width:
            malformed += 1
            continue
        name, version, release, arch, source_rpm = [field.strip() for field in fields[:5]]
        label = _clean(fields[5]) if with_modularity else None
        value = (_clean(source_rpm) or None, label)
        index.setdefault((name, version, release, arch), set()).add(value)
    return index, malformed


def _enrich(packages, index, with_modularity):
    total = 0
    matched = 0
    enriched = {}
    for name, entries in packages.items():
        copies = []
        for entry in entries:
            item = dict(entry)
            total += 1
            key = (name, item.get("version"), item.get("release"), item.get("arch"))
            values = index.get(key)
            if values is not None and len(values) == 1:
                source_rpm, label = next(iter(values))
                if source_rpm is not None:
                    item["source_rpm"] = source_rpm
                if with_modularity:
                    item["modularity_label"] = label
                matched += 1
            copies.append(item)
        enriched[name] = copies
    return enriched, total, matched


def collect(request):
    packages = request.get("packages") or {}
    entries_total = sum(len(entries) for entries in packages.values())
    if int(request.get("rpm_rc", 1)) != 0:
        return {
            "packages": packages,
            "observation": {
                "observation_state": "unavailable",
                "reason": REASON_QUERY_FAILED,
                "modularity_label_observed": False,
                "entries_total": entries_total,
                "entries_matched": 0,
                "lines_malformed": 0,
            },
        }
    with_modularity = bool(request.get("modularity_label_observed"))
    index, malformed = _index_rpm_lines(request.get("rpm_stdout") or "", with_modularity)
    enriched, total, matched = _enrich(packages, index, with_modularity)
    return {
        "packages": enriched,
        "observation": {
            "observation_state": "ready",
            "reason": "",
            "modularity_label_observed": with_modularity,
            "entries_total": total,
            "entries_matched": matched,
            "lines_malformed": malformed,
        },
    }


if __name__ == "__main__":
    print(json.dumps(collect(json.load(sys.stdin)), sort_keys=True, separators=(",", ":")))
