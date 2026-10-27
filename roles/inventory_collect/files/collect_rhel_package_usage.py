"""Uso de pacote pelo guest RHEL: leitura somente-leitura e sem executar binário.

Roda com o usuário de coleta, sem become; só o playbook do template dedicado a eleva (ver C1 abaixo). Fontes
permitidas:
- systemctl list-units/show: metadados de unidade;
- rpm -qf, rpm -q --requires, rpm -q --whatprovides: banco RPM, só leitura;
- rpm -q, rpm -q --provides, rpm -ql, rpm -q --whatrequires: banco RPM, só leitura, só para os
  pacotes que o request da origem A pede em ``reverse_dependency_packages``;
- uname -r: kernel em execução;
- só com ``usage_dimensions`` no request de A: ``busctl --system list --acquired``, ``systemctl show``
  da unidade de ativação D-Bus e do serviço de timer ativo, e leitura de arquivo: ativação D-Bus do
  pacote, ``/proc/mounts``, ``/proc/<pid>/{stat,exe,maps,fd,cmdline}``, ``/proc/<pid>/environ`` do
  interpretador (só os nomes das variáveis decidem; nenhum valor sai da sonda) e os agendamentos
  (``/etc/crontab``, ``/etc/anacrontab``, ``/etc/cron.*``, ``/var/spool/cron``, ``/var/spool/at``).

Não usa ldd (ele executa o loader) e não executa nenhum binário do produto. Cada comando tem tempo e
bytes limitados; arquivo só é lido quando é regular, até um limite de bytes. ``/proc`` de outro usuário
sem permissão não é lido: o processo conta como ilegível e a dimensão fica ``not_measured``.

Escopo declarado da medição (``coverage.scope``): unidades de serviço em execução, timers e
sockets ativos (``service``, ``timer``, ``socket``) e dependências RPM até a profundidade
``MAX_DEPTH`` a partir dos pacotes donos dessas unidades. A profundidade é o escopo, não um
corte: pacote além dela não é medido, e isso não torna a cobertura ``partial``. Por isso
``complete`` quer dizer "medido dentro deste escopo". Ausência de uso só vale dentro dele;
fora dele a sonda não afirma nada. Não cobre processo sem unidade, script manual nem biblioteca
carregada sem requisito declarado no RPM: é o risco de falso ``absent`` declarado no PR. Para os
pacotes pedidos pela origem A, as dimensões de uso abaixo medem processo vivo, agendamento e D-Bus;
script manual que não está rodando continua fora. Unidades ``.path`` e scripts de ``/etc/profile.d`` (rodados por
login, não agendados) também ficam fora deste escopo: a sonda não afirma ausência de uso por eles.

Corte que compromete a medição (limite de arestas, de nós, de tempo, de saída; nome ou caminho
que a sonda não representa; kernel sem pacote correspondente) aparece em ``coverage.reasons`` e
torna a cobertura ``partial``. Nenhum corte descarta dado em silêncio.

Dependentes reversos (``reverse_dependencies``) só existem quando o request os pede: para cada
pacote, quem o requer pelo nome, por cada capacidade que ele fornece (soname incluído) e por cada
arquivo dele. Pacote que não coube na medição fica ``unmeasured`` com o motivo, nunca "sem dependente".

Dimensões de uso (``usage_dimensions``) só existem quando o request de A as pede, para os pacotes de
``reverse_dependency_packages``. Cada pacote recebe ``used``, ``not_used`` ou ``not_measured`` em cada uma:

- ``dbus_activation``: nome do arquivo de ativação do pacote com dono agora, ou unidade do
  ``SystemdService`` ativa ou ativada desde o boot, é ``used``. Sem arquivo de ativação, ou com a
  ativação impossível (unidade inexistente ou mascarada, ``Exec`` falso sem unidade), é ``not_used``.
  Ativável sob demanda e sem ativação observada é ``not_measured``: unidade sem histórico não prova que
  nunca foi ativada, porque o systemd descarrega unidade inativa sem referência.
- ``processes``: executável, mapeamento, descritor aberto ou argumento de processo vivo que é arquivo
  do pacote é ``used``, comparando caminhos pelo usrmerge e pelo ``realpath``. Processo ilegível, lista
  restrita por ``hidepid`` ou corte por limite deixam ``not_measured``. Módulo de linguagem interpretada é
  lido sem ficar mapeado: interpretador (executável ou biblioteca embutida, como ``mod_wsgi``) cujo programa
  não se lê sem ambiguidade, não tem dono RPM ou tem caminho de import no ambiente deixa ``not_measured``
  o pacote com módulo daquela linguagem. Shell rodando script sem dono RPM ou pipe deixa
  todo pacote ``not_measured``. Shell interativo (com terminal) é uso manual, fora da medição, como o
  script manual que não está rodando. A sessão da própria coleta fica fora. Argumento do argv que nomeia executável
  do PATH do próprio processo impede ``not_used`` de todos os pacotes (``process_argument_unresolved``). Dentro do
  conjunto admitido por token, o nome vale pelo sufixo de opção curta colada (``-Itimedatex``) ou por caminho
  executável dentro do token. Fora dele, o token é dividido por ``:``, ``,``, ``;``, ``%``, ``=`` e espaço, e cada
  componente é julgado do mesmo jeito; componente com caractere que a sonda não interpreta (``$``, glob, aspas,
  parênteses, chaves) não pode ser julgado e bloqueia; componente com caminho relativo (``./x``, ``../x``, ``bin/x``,
  ``-I./x``) também bloqueia, porque o diretório atual do processo não se sabe. ``argv[0]`` (o título do processo,
  como ``sshd: root@pts/0``)
  e o script que um interpretador ou shell lê (``python3 -s script``) ficam fora. O PATH é o de
  ``/proc/<pid>/environ``, o ambiente do exec: ambiente alterado depois do exec não aparece; sem a variável, vale o
  padrão do sistema. Environ ilegível, ou entrada relativa no PATH, bloqueia quando há palavra que poderia nomear
  programa; o custo é bloquear por sufixo de uma letra (``-cf`` tem ``f``). Ler o PATH da unidade systemd fica como
  melhoria futura. Token que não nomeia nada (``--format=json``, ``chronyd -u chrony``) não bloqueia; o título
  ``sshd: root@pts/0`` passado como argumento (não como ``argv[0]``) bloqueia, porque ``root@pts/0`` tem forma de caminho
  relativo. Os caminhos que
  o argumento nomeia continuam como uso. Código inline de qualquer interpretador (``python3 -c``, ``perl -e``,
  ``node -e``, ``-E``, ``--eval``, ``-r``, e o mesmo em interpretador desconhecido com essas opções) deixa todo
  pacote ``not_measured`` com ``interpreter_inline_code``, como o shell.
- ``schedules``: crontab, cron.d, spool de usuário, script de cron.*, job de ``at`` e Exec* do serviço de
  timer ativo que citam arquivo do pacote são ``used``. Uma entrada só resolve quando está na lista fechada
  abaixo; qualquer outra forma fica ``not_measured``:
  1. programa absoluto com dono RPM e sem PATH próprio, cujos argumentos não sejam caminho executável,
     ``VAR=valor``, opção de login, separador, palavra sem caminho (que o programa pode rodar pelo PATH) nem
     opção curta com restante colado que contenha programa do PATH efetivo (``-Izstd``, ``-essh``);
  2. ``run-parts`` com um diretório ``/etc/cron.*``;
  3. ``nice``, ``nohup`` ou ``ionice``, uma vez, com opções numéricas que a sonda conhece, seguidos da forma 1
     ou da 2 (o ``nice run-parts`` do anacrontab padrão);
  4. as formas padrão do RHEL já testadas (``0hourly``, anacrontab, ``/etc/crontab``).
  Executor de login ou de ambiente (``su``, ``runuser``, ``sudo``, ``ssh``, ``machinectl``, ``pbrun``, ``dzdo``,
  ``ksu``, ``sesu``, ``scl``, ``env``) nunca resolve, mesmo com comando absoluto. Entrada sem identificação
  (variável, expansão, separador, aspas, caminho relativo, palavra sem caminho, job de ``at``, timer não
  resolvido, fonte ilegível) fica ``schedule_command_unresolved``; forma identificada fora da lista fica
  ``schedule_form_not_allowlisted``. Interpretador só passa na forma
  ``<interpretador> <script absoluto com dono>``: opção ou ambiente antes do script (``bash -l``, ``sh -i``) fica
  fora da lista. Todo token precisa estar no conjunto de caracteres admitido: opção sem barra, ``--opt=`` vazio,
  caminho absoluto único (sem ``//`` nem ``..``) ou ``--opt=`` com esse caminho. Qualquer outro caractere ou
  estrutura (``:``, ``,``, ``;``, ``=`` no caminho, ``$``, glob, ``~``, ``%``, chaves, aspas) fica sem resolução.
  Dentro dessa lista, barra colada a opção sem ``=`` (``-I/caminho``) fica fora da lista, e valor de opção que é
  palavra (``--opt=job``) fica sem resolução, como a palavra solta. Programa executado direto (``cron.d``, ``cron.*`` e ``ExecStart`` de timer)
  tem o ``#!`` lido: interpretador absoluto com dono RPM é uso do pacote dono; ``#!/usr/bin/env X`` segue a regra
  de palavra; cabeçalho ilegível, relativo ou sem dono fica sem resolução.
  Executor guiado por configuração (``postrotate`` do logrotate, ``rc.local``, ``RUN`` do udev, dispatcher do
  NetworkManager) fica fora da medição.

Na dúvida, ``not_measured``: falha parcial ou entrada que a sonda não resolve nunca vira ausência de uso.

Privilégio da coleta (C1): por padrão o coletor roda sem ``become`` (``ec2-user``). Com isso, ``/proc`` de outro
usuário e o spool de cron podem ficar ilegíveis, e ``processes`` e ``schedules`` só provam ``used``; ``not_used`` não
sai nessas dimensões. Só o template dedicado ``samurai_inventory_source_privileged_probe`` (decisão do dono, D1) roda
a sonda como root, com o request de omissões de A e o ``privileged_measurement`` desta execução. O recibo leva
``effective_uid`` (o euid real da sonda), e o REE recusa recibo com root fora desse template. No código, root só muda uma coisa: com ``euid == 0``, ``_proc_restricted`` não marca a lista como restrita
por ``hidepid``. A regra não muda: qualquer motivo de ``not_measured`` (processo ilegível, corte por limite, entrada sem resolução, código inline de
interpretador, fonte ilegível) continua impedindo ``not_used``. Root não garante ``not_used``; só retira um motivo
de ``not_measured``. A sonda não escolhe o privilégio; ela só o recebe.
"""
import collections
import datetime
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time

SCHEMA = "samurai.rhel_package_usage_observation/v1"
LIMIT = 16384
MAX_UNITS = 64
MAX_DEPTH = 2
MAX_NODES = 300
TIME_BUDGET_S = 60
# Acima do timeout de 8 s de command(): o SIGKILL do grupo já cortou o que obedece ao sinal.
JOIN_TIMEOUT_S = 10
# N5 #81: cap 500 cortou em 75 de 125 nós. Depth 2 estimado ~730 pares; 1200 cobre até 13 pares/nó nos restantes.
EDGE_CAP = 1200
KERNEL_NAMES = ("kernel", "kernel-core", "kernel-modules")
UNIT_KINDS = ("service", "timer", "socket")

# Primeiro caractere alfanumérico: nome que começa com "-" nunca vira opção de systemctl.
UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,127}\.(service|timer|socket)$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,127}\.service$")
MODEL_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.:-]{0,126})@\.service$")
INSTANCE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.:-]{0,126})@[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}\.service$")
MODEL_INSTANCE = "samurai-probe-model"
PATH_RE = re.compile(r"^/[A-Za-z0-9._/+@-]{1,255}$")
EXEC_PATH_RE = re.compile(r"path=([^ ;}]+)")
RELEASE_RE = re.compile(r"^[0-9][A-Za-z0-9_.+~-]{0,127}$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
SKIPPED_REQUIREMENTS = ("rpmlib(", "config(")
# Mensagem do rpm -qf para binário sem dono. O rpmlog emite NOTICE no stdout por padrão; esta forma é defensiva.
UNOWNED_RE = re.compile(r"file .+ is not owned by any package")
MAX_REVERSE_PACKAGES = 32
# Nome, capacidades e arquivos de um pacote numa só consulta; acima disso o pacote fica não medido.
MAX_REVERSE_ARGUMENTS = 512
REVERSE_TIME_BUDGET_S = 30
NOT_REQUIRED_RE = re.compile(r"no package requires .+")
NOT_INSTALLED_RE = re.compile(r"package .+ is not installed")

USED, NOT_USED, NOT_MEASURED = "used", "not_used", "not_measured"
# Entrada agendada: na lista fechada, sem identificação (``UNRESOLVED``) ou fora da lista (``OFF_LIST``).
RESOLVED, UNRESOLVED, OFF_LIST = "resolved", "unresolved", "off_list"
USAGE_DIMENSIONS = ("dbus_activation", "processes", "schedules")
DIMENSION_RE = re.compile(r"^[a-z][a-z_]{0,31}$")
MAX_DIMENSIONS = 8
DIMENSION_TIME_BUDGET_S = 30
# Prazo duro (SIGALRM) acima do cooperativo: leitura presa e interrompível não segura a sonda.
DIMENSION_GRACE_S = 10
# Arquivo de ativação, crontab, script agendado e cmdline; maps tem limite próprio por processo.
FILE_LIMIT = 65536
MAPS_LIMIT = 1048576
MAX_PROCESSES = 4096
MAX_FDS = 4096
MAX_SCHEDULE_FILES = 256
MAX_OWNER_QUERIES = 128
MAX_MATCHES = 8
PF_KTHREAD = 0x00200000
# Já na forma usrmerge (``/usr/...``): o caminho do pacote e o observado passam por ``_usrmerge``.
DBUS_SYSTEM_DIRS = ("/usr/share/dbus-1/system-services/", "/usr/lib/dbus-1/system-services/")
DBUS_SESSION_DIRS = ("/usr/share/dbus-1/services/",)
BUS_NAME_RE = re.compile(r"^[A-Za-z_-][A-Za-z0-9_-]*(\.[A-Za-z_-][A-Za-z0-9_-]*)+$")
FALSE_EXECS = frozenset({"/bin/false", "/usr/bin/false"})
# No RHEL 8 e 9, /bin, /sbin, /lib e /lib64 são links para /usr: o rpm -ql e o maps podem usar formas diferentes.
USRMERGE = (("/bin/", "/usr/bin/"), ("/sbin/", "/usr/sbin/"), ("/lib64/", "/usr/lib64/"), ("/lib/", "/usr/lib/"))
# Interpretador: executável, biblioteca que o embute, extensão de módulo (lido sem ficar mapeado), variável
# de ambiente que muda o que é importado, e a gramática curta de opções que a sonda sabe ler sem ambiguidade.
Interpreter = collections.namedtuple("Interpreter", (
    "language", "exe", "library", "suffixes", "env", "no_arg", "with_arg", "inline", "long_no_arg", "long_with_arg"))
INTERPRETERS = (
    Interpreter("python", re.compile(r"^(platform-)?(python|pypy)[0-9.]*[dmu]*$"),
                re.compile(r"^libpython[0-9.]*[dmu]*\.so"), (".py", ".pyc", ".pyo"),
                ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"),
                "bBdEhiIOqsSuvVxR", "WX", "cm", ("--help", "--version"), ("--check-hash-based-pycs",)),
    Interpreter("perl", re.compile(r"^perl[0-9.]*$"), re.compile(r"^libperl\.so"), (".pm", ".pl"),
                ("PERL5LIB", "PERLLIB", "PERL5OPT"), "wTtWXU", "", "eEMmxI", (), ()),
    Interpreter("ruby", re.compile(r"^ruby[0-9.]*$"), re.compile(r"^libruby[0-9.]*\.so"), (".rb",),
                ("RUBYLIB", "RUBYOPT"), "wv", "", "eIr", (), ()),
    Interpreter("php", re.compile(r"^(php|php-cgi|php-fpm)[0-9.]*$"), re.compile(r"^(libphp|mod_php)[0-9]*\.so"),
                (".php", ".inc"), ("PHPRC", "PHP_INI_SCAN_DIR"), "", "", "aBdfFRr", (), ()),
    Interpreter("node", re.compile(r"^(node|nodejs)[0-9.]*$"), re.compile(r"^libnode\.so"), (".js", ".mjs", ".cjs"),
                ("NODE_PATH", "NODE_OPTIONS"), "", "", "epr", (), ()),
    Interpreter("lua", re.compile(r"^lua(jit)?[0-9.]*$"), re.compile(r"^lib(lua|luajit)[0-9.-]*\.so"), (".lua",),
                ("LUA_PATH", "LUA_CPATH", "LUA_INIT"), "", "", "eil", (), ()),
    Interpreter("tcl", re.compile(r"^(tclsh|wish)[0-9.]*$"), re.compile(r"^libtcl[0-9.]*\.so"), (".tcl",),
                ("TCLLIBPATH",), "", "", "", (), ()),
)
# Shell rodando script sem dono RPM pode chamar qualquer pacote: vale para todos, não para uma linguagem.
SHELL = Interpreter("shell", re.compile(r"^(ba|da|k|mk|z|tc|c)?sh(93)?$"), None, (), ("BASH_ENV", "ENV"),
                    "abefhkmnptuvxBCEHPTilr", "oO", "cs",
                    ("--norc", "--noprofile", "--login", "--posix", "--noediting", "--restricted", "--verbose"),
                    ("--rcfile", "--init-file"))
CRON_FILES = ("/etc/crontab", "/etc/anacrontab")
CRON_TABLE_DIRS = ("/etc/cron.d", "/var/spool/cron")
CRON_SCRIPT_DIRS = ("/etc/cron.hourly", "/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly")
AT_SPOOL = "/var/spool/at"
# Nomes que cron e run-parts ignoram.
SKIPPED_SUFFIXES = ("~", ",", ".rpmsave", ".rpmorig", ".rpmnew", ".swp", ".cfsaved")
STANDARD_BIN_DIRS = ("/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin", "/usr/local/sbin")
# PATH do cronie quando o crontab não declara outro.
CRON_DEFAULT_PATH = ("/usr/bin", "/bin")
CRON_KEYWORDS = frozenset({"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight",
                           "@hourly"})
CRON_FIELD_RE = re.compile(r"^[0-9A-Za-z*,/-]+$")
ENV_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
# Variável do crontab que não muda qual programa roda nem o que ele carrega.
SAFE_CRON_ENV = frozenset({"MAILTO", "MAILFROM", "HOME", "LOGNAME", "LANG", "LC_ALL", "CRON_TZ", "TZ",
                           "RANDOM_DELAY", "START_HOURS_RANGE", "CONTENT_TYPE", "CONTENT_TRANSFER_ENCODING"})
STANDARD_SHELLS = frozenset({"/bin/sh", "/bin/bash", "/usr/bin/sh", "/usr/bin/bash"})
# Redirecionamento simples (descritor, >, >> ou <, e destino literal ou &N) não muda o que o comando executa.
SIMPLE_REDIRECT_RE = re.compile(r"(?<!\S)[0-9]?(?:>>|>|<)\s*(?:&[0-9]|/[A-Za-z0-9._/+@-]+)(?!\S)")
# O que a sonda não resolve de forma determinística numa linha de comando: separador, expansão, aspas,
# glob, comentário, ``%`` do cron e controle. Achou, a entrada fica sem resolução.
UNRESOLVED_RE = re.compile(r"[;|&$`'\"(){}\[\]*?~\\!#%<>\x00-\x1f\x7f]")
TOKEN_RE = re.compile(r"[^\s;|&<>()`'\"=\x00-\x1f]+")
# Só nice, nohup e ionice são lidos como quem executa outro comando, e uma vez só, com a opção numérica que a
# sonda conhece. O comando que eles executam segue as mesmas regras da entrada.
EXEC_WRAPPERS = frozenset({"nice", "nohup", "ionice"})
NICE_RE = re.compile(r"-n?-?[0-9]+|--adjustment=-?[0-9]+")
IONICE_RE = re.compile(r"-[cn][0-9]+|-t")
# Executor que roda o comando com o perfil ou o ambiente de outro usuário ou máquina: fora da lista, sempre.
LOGIN_EXECUTORS = frozenset({"su", "runuser", "sudo", "ssh", "machinectl", "pbrun", "dzdo", "ksu", "sesu", "scl",
                             "env"})
# Opção de login: ``-``, ``--login``, ``-l``, ``-i`` ou agrupada com essas letras (``-lc``, ``-iu``).
LOGIN_OPTION_RE = re.compile(r"^(-|--login(=.*)?|-[A-Za-z]*[li][A-Za-z]*)$")
# Gramática de token da linha de comando: opção sem barra (``-x``, ``--opt``) e opção com valor após ``=``. Barra
# colada a opção sem ``=`` (``-I/caminho``) não casa: é caminho executável escondido e o token fica fora da lista.
OPTION_RE = re.compile(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*")
OPTION_VALUE_RE = re.compile(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*=(.*)")
# Opção curta com restante colado (``-Izstd``): o restante não pode conter programa do PATH efetivo da entrada.
SHORT_CLUSTER_RE = re.compile(r"-([A-Za-z0-9])([A-Za-z0-9_-]+)")
# Cabeçalho ``#!`` lido do início de um programa executado direto (script agendado).
SHEBANG_LIMIT = 512
# Conjunto de caracteres admitido num token: sem ``:``, ``,``, ``;``, ``=`` no caminho, ``$``, glob, ``~``, ``%``, chaves.
CLEAN_PATH_RE = re.compile(r"/[A-Za-z0-9._+@/-]+")
CHARSET_RE = re.compile(r"[A-Za-z0-9._+@/-]*")
EMPTY_VALUE_RE = re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*=")
# Opção curta ou longa colada a caminho, sem ``=`` (``-I/caminho``): forma que a gramática descreve como fora da lista.
GLUED_OPTION_PATH_RE = re.compile(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*(/.*)")
# PATH que o systemd dá ao serviço sem Environment=PATH.
SYSTEMD_DEFAULT_PATH = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin")
TIMER_SERVICE_PROPERTIES = ("ExecCondition,ExecStartPre,ExecStart,ExecStartPost,ExecStop,ExecStopPost,"
                            "Environment,EnvironmentFiles")
EXEC_LINE_RE = re.compile(r"^\{ path=(\S+) ; argv\[\]=(.*?) ; ignore_errors=(?:yes|no) ;")
# Variável de ambiente de unidade que muda o que o programa roda ou carrega.
RISKY_ENV_RE = re.compile(r"(?:^|[\s\"'])(PATH|LD_PRELOAD|LD_LIBRARY_PATH|LD_AUDIT|BASH_ENV|ENV|LUA_[A-Z0-9_]*|%s)=" % (
    "|".join(name for spec in INTERPRETERS for name in spec.env)))
DELETED = " (deleted)"


def _kill_group(process):
    """SIGKILL no grupo do processo. Com ``start_new_session`` o pid é o pgid: alcança o ``timeout`` e os netos."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass  # o grupo já não existe


def _capture(process, name, captured):
    """Lê ``name`` até LIMIT+1 bytes. Acima do limite, mata o grupo para nenhum neto continuar escrevendo."""
    data = getattr(process, name).read(LIMIT + 1)
    cut = len(data) > LIMIT
    if cut:
        _kill_group(process)
    captured[name] = (data[:LIMIT].decode("utf-8", "replace"), cut)


def read(argv):
    """Executa sem shell, em grupo próprio. stdout e stderr chegam separados; cada um é cortado em LIMIT bytes.

    Stream que não fecha em JOIN_TIMEOUT_S (neto que escapou do grupo e segura o pipe) não vira resultado:
    o grupo morre, rc é 124 e ``truncated`` fica True. Stream sem captura nunca é lido como vazio.
    """
    receipt = {"argv": argv, "rc": None, "stdout": "", "stderr": "", "truncated": False}
    process = None
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, "LC_ALL": "C"}, start_new_session=True)
        captured = {}
        readers = [threading.Thread(target=_capture, args=(process, name, captured), daemon=True)
                   for name in ("stdout", "stderr")]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + JOIN_TIMEOUT_S
        for reader in readers:
            reader.join(max(0.0, deadline - time.monotonic()))
        if {"stdout", "stderr"} <= captured.keys():
            process.wait(timeout=2)
            receipt.update(rc=process.returncode, stdout=captured["stdout"][0],
                           stderr=captured["stderr"][0],
                           truncated=captured["stdout"][1] or captured["stderr"][1])
        else:
            _kill_group(process)
            process.wait(timeout=2)
            receipt.update(rc=124, truncated=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if process is not None:
            _kill_group(process)
        receipt.update(stderr=str(exc)[:1024], truncated=True)
    return receipt


def command(argv):
    return read(["timeout", "--signal=KILL", "8", *argv])


def _unit(name, status, reason, binaries=(), packages=()):
    return {"unit": name, "status": status, "reason": reason,
            "binaries": sorted(binaries), "packages": sorted(packages)}


def _exec_paths(name, run):
    """Caminhos de ExecStart lidos com ``systemctl show -- <nome>``: (caminhos, None) ou (None, motivo)."""
    show = run(["systemctl", "show", "--no-pager", "--property=ExecStart,FragmentPath", "--", name])
    if show["rc"] != 0 or show["truncated"]:
        return None, "command_failed"
    raw = set()
    for line in show["stdout"].splitlines():
        if line.startswith("ExecStart="):
            raw.update(EXEC_PATH_RE.findall(line))
    paths = {path for path in raw if PATH_RE.match(path)}
    if raw - paths:
        return None, "unsupported_exec_path"
    if not paths:
        return None, "no_exec_start"
    return sorted(paths), None


def _owner_lookup(path, run):
    """Donos de um arquivo: (nomes, None), (None, "unowned_binary") ou (None, "command_failed").

    rc 1 sem stderr, ou com a mensagem «is not owned by any package», é sem dono. Outro rc 1 é falha
    (rpmdb); rc 0 sem nome também é falha, nunca arquivo sem dono.
    """
    owner = run(["rpm", "-qf", "--qf", "%{NAME}\n", path])
    if owner["truncated"] or owner["rc"] not in (0, 1):
        return None, "command_failed"
    if owner["rc"] == 1:
        if owner["stderr"] and not UNOWNED_RE.fullmatch(owner["stderr"].strip()):
            return None, "command_failed"
        return None, "unowned_binary"
    names = _package_names(owner["stdout"])
    if not names:
        return None, "command_failed"
    return names, None


def _owners(name, paths, run):
    """Pacotes donos dos binários: resolved, ou unresolved com o motivo real de ``_owner_lookup``."""
    packages = set()
    for path in paths:
        names, reason = _owner_lookup(path, run)
        if reason:
            return _unit(name, "unresolved", reason)
        packages.update(names)
    return _unit(name, "resolved", None, paths, packages)


def _inspect_unit(name, run):
    paths, reason = _exec_paths(name, run)
    if reason:
        return _unit(name, "unresolved", reason)
    return _owners(name, paths, run)


def _requirement_names(stdout):
    names = []
    for line in stdout.splitlines():
        parts = line.split()
        if parts and not parts[0].startswith(SKIPPED_REQUIREMENTS) and parts[0] not in names:
            names.append(parts[0])
    return names


def _failure_reason(result):
    return "timeout" if result["rc"] == 124 else "command_failed"


def _package_names(stdout):
    """Nomes de pacote da saída do rpm, sem repetição. Linha que não é nome nunca entra."""
    names = []
    for line in stdout.splitlines():
        name = line.strip()
        if PACKAGE_RE.match(name) and name not in names:
            names.append(name)
    return names


def _whatprovides(requirement, run):
    """(provedores, None), ou (None, motivo) quando a leitura falhou.

    rc 1 sem stderr é "ninguém provê", e rc 1 com stderr igual à mensagem do rpm para este requisito também.
    Qualquer outro stderr com rc 1 é falha: tratar como ausência esconderia aresta.
    """
    # "--" antes do nome: requisito que começa com "-" nunca vira opção; --qf precisa vir antes dele.
    result = run(["rpm", "-q", "--whatprovides", "--qf", "%{NAME}\n", "--", requirement])
    if result["truncated"] or result["rc"] not in (0, 1):
        return None, _failure_reason(result)
    if result["rc"] == 1:
        if result["stderr"] and result["stderr"].strip() != f"no package provides {requirement}":
            return None, "command_failed"
        return [], None
    names = _package_names(result["stdout"])
    if not names:
        return None, "command_failed"
    return names, None


def _elapsed_ms(since):
    """Milissegundos inteiros desde ``since`` (relógio monotônico). Só mede; não decide corte."""
    return int((time.monotonic() - since) * 1000)


def _closure(seed, run, started):
    """Dependências a partir dos pacotes donos, em largura, com limite de profundidade e nós.

    Aresta é o par (pacote, provedor): repetir o par só soma em ``duplicate_edges``. Requisito sem
    provedor não gera aresta nem nó (``requirements_without_provider``). Cada requisito é consultado
    uma vez na execução; resposta truncada ou com erro corta a medição e não entra no cache.
    """
    begun = time.monotonic()
    edges, nodes = [], 0
    visited = set(seed)
    recorded = set()
    providers_of = {}
    counts = {"requirement_queries": 0, "requirement_cache_hits": 0, "duplicate_edges": 0,
              "requirements_without_provider": 0}
    queue = collections.deque((package, 0) for package in sorted(seed))
    truncated, reason = False, None
    while queue:
        package, depth = queue.popleft()
        if depth >= MAX_DEPTH:
            continue
        if nodes >= MAX_NODES:
            truncated, reason = True, "node_budget"
            break
        if time.monotonic() - started > TIME_BUDGET_S:
            truncated, reason = True, "time_budget"
            break
        nodes += 1
        requires = run(["rpm", "-q", "--requires", "--", package])
        if requires["rc"] != 0 or requires["truncated"]:
            truncated, reason = True, _failure_reason(requires)
            break
        for requirement in _requirement_names(requires["stdout"]):
            if time.monotonic() - started > TIME_BUDGET_S:
                truncated, reason = True, "time_budget"
                break
            if requirement in providers_of:
                counts["requirement_cache_hits"] += 1
            else:
                providers, failure = _whatprovides(requirement, run)
                if failure:
                    truncated, reason = True, failure
                    break
                if not providers:
                    counts["requirements_without_provider"] += 1
                providers_of[requirement] = providers
                counts["requirement_queries"] += 1
            for provider in providers_of[requirement]:
                if provider == package:
                    continue
                if (package, provider) in recorded:
                    counts["duplicate_edges"] += 1
                    continue
                if len(edges) >= EDGE_CAP:
                    truncated, reason = True, "edge_budget"
                    break
                recorded.add((package, provider))
                edges.append({"from": package, "to": provider, "depth": depth + 1})
                if provider not in visited:
                    visited.add(provider)
                    queue.append((provider, depth + 1))
            if truncated:
                break
        if truncated:
            break
    return {"max_depth": MAX_DEPTH, "max_nodes": MAX_NODES, "nodes": nodes,
            "truncated": truncated, "reason": reason, "edges": edges, "elapsed_ms": _elapsed_ms(begun), **counts}


def _kernel(run):
    release = run(["uname", "-r"])
    running = release["stdout"].strip()
    if release["rc"] != 0 or release["truncated"] or not RELEASE_RE.match(running):
        running = None
    entries = run(["rpm", "-q", "--qf", "%{NAME}|%{VERSION}|%{RELEASE}\n", *KERNEL_NAMES])
    packages = []
    if not (entries["truncated"] or entries["rc"] == 124):
        for line in entries["stdout"].splitlines():
            parts = line.strip().split("|")
            if len(parts) == 3 and parts[0] in KERNEL_NAMES:
                packages.append({"name": parts[0], "version": parts[1], "release": parts[2]})
    return running, packages, (running is None), (entries["truncated"] or entries["rc"] == 124)


def _inspect_trigger(name, run):
    """Leitura pelo modelo ``x@samurai-probe-model.service`` não vê drop-in por instância; a instância vem primeiro."""
    kind = "timer" if name.endswith(".timer") else "socket"
    prop = "Unit" if kind == "timer" else "Triggers"
    target = None
    via_model = False
    shown = run(["systemctl", "show", "--no-pager", f"--property={prop}", "--", name])
    if shown["rc"] != 0 or shown["truncated"]:
        entry = _unit(name, "unresolved", "command_failed")
    else:
        value = shown["stdout"].strip()
        resolved = value.split("=", 1)[1].strip() if "=" in value else ""
        if not resolved:
            resolved = name.rsplit(".", 1)[0] + ".service"
        model = MODEL_RE.match(resolved)
        instance = INSTANCE_RE.match(resolved)
        if len(resolved.split()) != 1 or not UNIT_RE.match(resolved):
            entry = _unit(name, "unresolved", "unsupported_trigger")
        elif "@" in resolved and not (model or instance):
            entry = _unit(name, "unresolved", "template_unit")
        else:
            target = resolved
            if instance:
                paths, reason = _exec_paths(resolved, run)
                if reason is None:
                    inspected = _owners(resolved, paths, run)
                elif reason == "no_exec_start":
                    # Só instância sem ExecStart cai no modelo, marcado; outra falha fica com o motivo dela.
                    via_model = True
                    inspected = _inspect_unit(f"{instance.group(1)}@{MODEL_INSTANCE}.service", run)
                else:
                    inspected = _unit(resolved, "unresolved", reason)
            elif model:
                via_model = True
                inspected = _inspect_unit(f"{model.group(1)}@{MODEL_INSTANCE}.service", run)
            else:
                inspected = _inspect_unit(resolved, run)
            entry = _unit(name, inspected["status"], inspected["reason"],
                          inspected["binaries"], inspected["packages"])
    entry["kind"] = kind
    entry["target"] = target
    entry["via_model"] = via_model
    return entry


def _trigger_units(run, started):
    listed = run(["systemctl", "list-units", "--type=timer,socket", "--state=active",
                  "--no-legend", "--plain", "--no-pager"])
    if listed["rc"] != 0 or listed["truncated"]:
        return [], ["trigger_list_unavailable"]
    names = []
    reasons = []
    for line in listed["stdout"].splitlines():
        token = line.split()[0] if line.split() else ""
        if UNIT_RE.match(token) and token.endswith((".timer", ".socket")):
            names.append(token)
        elif token and not UNIT_RE.match(token):
            reasons.append("unit_name_unsupported")
    if len(names) > MAX_UNITS:
        reasons.append("triggers_over_limit")
        names = names[:MAX_UNITS]
    entries = []
    for name in names:
        if time.monotonic() - started > TIME_BUDGET_S:
            entries.append({**_unit(name, "unresolved", "time_budget"), "kind": "timer" if name.endswith(".timer") else "socket", "target": None, "via_model": False})
        else:
            entries.append(_inspect_trigger(name, run))
    return entries, reasons


def _release_of(running):
    """Versão-release do kernel em execução, sem o sufixo de arquitetura."""
    return running.rsplit(".", 1)[0] if running.endswith((".x86_64", ".aarch64")) else running


def _scope():
    """Escopo declarado da medição: o que ``complete`` quer dizer. O controlador confere este valor."""
    return {"dependency_depth": MAX_DEPTH, "unit_kinds": sorted(UNIT_KINDS)}


def _reverse_request(request):
    """Pacotes pedidos para dependente reverso: ``None`` sem pedido, ``[]`` com pedido inválido."""
    if "reverse_dependency_packages" not in request:
        return None
    names = request["reverse_dependency_packages"]
    if (not isinstance(names, list) or not 0 < len(names) <= MAX_REVERSE_PACKAGES
            or len(set(map(str, names))) != len(names)
            or not all(isinstance(name, str) and PACKAGE_RE.fullmatch(name) for name in names)):
        return []
    return names


def _unmeasured(reason):
    return {"status": "unmeasured", "reason": reason}


def _installed(package, run):
    """``True`` instalado, ``False`` ausente do banco RPM, ``None`` quando a leitura falhou."""
    result = run(["rpm", "-q", "--qf", "%{NAME}\n", "--", package])
    if result["truncated"] or result["rc"] not in (0, 1):
        return None
    if result["rc"] == 1:
        message = (result["stdout"] + result["stderr"]).strip()
        return False if NOT_INSTALLED_RE.fullmatch(message) else None
    return True if _package_names(result["stdout"]) == [package] else None


def _provided_capabilities(package, run):
    """Capacidades que o pacote fornece, soname incluído: (lista, None) ou (None, motivo)."""
    result = run(["rpm", "-q", "--provides", "--", package])
    if result["rc"] != 0 or result["truncated"]:
        return None, _failure_reason(result)
    names = []
    for line in result["stdout"].splitlines():
        parts = line.split()
        if parts and parts[0] not in names:
            names.append(parts[0])
    return names, None


def _owned_files(package, run):
    """Arquivos do pacote: (lista, None) ou (None, motivo). Linha que não é caminho absoluto corta a medição."""
    result = run(["rpm", "-ql", "--", package])
    if result["rc"] != 0 or result["truncated"]:
        return None, _failure_reason(result)
    files = []
    for line in result["stdout"].splitlines():
        path = line.strip()
        if not path or path == "(contains no files)":
            continue
        if not path.startswith("/"):
            return None, "unsupported_file_path"
        files.append(path)
    return files, None


def _requirers(package, arguments, run):
    """Quem requer qualquer argumento (nome, capacidade ou arquivo): (lista, None) ou (None, motivo).

    rc do ``rpm -q`` conta os argumentos sem resposta, então rc diferente de zero não é falha. Falha é
    saída cortada, tempo esgotado, stderr com outra mensagem ou linha que não é nome de pacote.
    """
    result = run(["rpm", "-q", "--whatrequires", "--qf", "%{NAME}\n", "--", *arguments])
    if result["truncated"] or result["rc"] == 124:
        return None, _failure_reason(result)
    if any(line.strip() and not NOT_REQUIRED_RE.fullmatch(line.strip())
           for line in result["stderr"].splitlines()):
        return None, "command_failed"
    names = set()
    for line in result["stdout"].splitlines():
        text = line.strip()
        if not text or NOT_REQUIRED_RE.fullmatch(text):
            continue
        if not PACKAGE_RE.fullmatch(text):
            return None, "command_failed"
        names.add(text)
    names.discard(package)
    return sorted(names), None


def _reverse_of(package, run):
    """Dependentes reversos de um pacote e a lista de arquivos dele (``None`` quando não foi lida)."""
    installed = _installed(package, run)
    if installed is None:
        return _unmeasured("command_failed"), None
    if installed is False:
        return {"status": "not_installed"}, None
    capabilities, reason = _provided_capabilities(package, run)
    if reason:
        return _unmeasured(reason), None
    files, reason = _owned_files(package, run)
    if reason:
        return _unmeasured(reason), None
    arguments = list(dict.fromkeys([package, *capabilities, *files]))
    if len(arguments) > MAX_REVERSE_ARGUMENTS:
        return _unmeasured("arguments_over_limit"), files
    requirers, reason = _requirers(package, arguments, run)
    if reason:
        return _unmeasured(reason), files
    return {"status": "measured", "requirers": requirers,
            "capabilities": len(capabilities), "files": len(files)}, files


def _reverse_dependencies(packages, run):
    """Dependentes reversos dos pacotes pedidos, com cobertura própria, e os arquivos de cada pacote."""
    started = time.monotonic()
    measured, files = {}, {}
    for package in packages:
        if time.monotonic() - started > REVERSE_TIME_BUDGET_S:
            measured[package], files[package] = _unmeasured("time_budget"), None
        else:
            measured[package], files[package] = _reverse_of(package, run)
    reasons = sorted({entry["reason"] for entry in measured.values() if entry["status"] == "unmeasured"})
    if not packages:
        reasons = ["request_invalid"]
    return {"packages": measured, "elapsed_ms": _elapsed_ms(started),
            "coverage": {"status": "partial" if reasons else "complete", "reasons": reasons}}, files


def _dimension_request(request):
    """Dimensões de uso pedidas: ``None`` sem pedido, ``[]`` com pedido inválido."""
    if "usage_dimensions" not in request:
        return None
    names = request["usage_dimensions"]
    if (not isinstance(names, list) or not 0 < len(names) <= MAX_DIMENSIONS
            or not all(isinstance(name, str) and DIMENSION_RE.fullmatch(name) for name in names)
            or len(set(names)) != len(names)):
        return []
    return names


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class NotRegularFile(OSError):
    """Caminho que não é arquivo regular (diretório, FIFO, dispositivo): nunca é aberto."""


def _host_call(function, path):
    """No Python 3.6, caminho com NUL levanta ValueError em vez de OSError: vira OSError aqui."""
    try:
        return function(path)
    except ValueError as exc:
        raise OSError(22, str(exc))


class HostFiles:
    """Leitura local, sem escrita. Arquivo só é aberto quando é regular e lido até ``limit`` bytes."""

    def listdir(self, path):
        return sorted(_host_call(os.listdir, path))

    def readlink(self, path):
        return _host_call(os.readlink, path)

    def realpath(self, path):
        return _host_call(os.path.realpath, path)

    def mode(self, path):
        return _host_call(os.stat, path).st_mode

    def is_file(self, path):
        try:
            return os.path.isfile(path)
        except ValueError:
            return False

    def read(self, path, limit):
        """(texto, cortado). Dispositivo e FIFO nem são abertos; O_NONBLOCK cobre a troca entre stat e open."""
        if not stat.S_ISREG(_host_call(os.stat, path).st_mode):
            raise NotRegularFile(path)
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise NotRegularFile(path)
            chunks, size = [], 0
            while size <= limit:
                chunk = os.read(fd, limit + 1 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        finally:
            os.close(fd)
        data = b"".join(chunks)
        return data[:limit].decode("utf-8", "replace"), len(data) > limit

    def identity(self):
        return {"pid": os.getpid(), "session": os.getsid(0), "euid": os.geteuid()}


def _usrmerge(path):
    for legacy, merged in USRMERGE:
        if path.startswith(legacy):
            return merged + path[len(legacy):]
    return path


class _Paths:
    """Forma comparável de um caminho: usrmerge e ``realpath`` dos dois lados (``rpm -ql`` e o observado)."""

    def __init__(self, fs):
        self.fs, self.cache = fs, {}

    def forms(self, path):
        if path not in self.cache:
            merged = _usrmerge(path)
            try:
                real = _usrmerge(self.fs.realpath(merged))
            except OSError:
                real = merged
            self.cache[path] = (merged,) if real == merged else (merged, real)
        return self.cache[path]

    def final(self, path):
        """O arquivo que de fato roda: a última forma, depois do ``realpath``."""
        return self.forms(path)[-1]

    def index(self, package_files):
        index = {}
        for package, files in package_files.items():
            for path in files:
                for form in self.forms(path):
                    index.setdefault(form, set()).add(package)
        return index


class _Matches:
    """Arquivos de pacote pedido achados em uso: contagem por pacote e até MAX_MATCHES exemplos.

    ``add`` compara pelo usrmerge e pelo ``realpath``; ``add_text`` (texto de crontab e script) só pelo
    usrmerge, porque o programa que a entrada roda já entra pelo ``add`` quando é resolvido.
    """

    def __init__(self, index, paths=None):
        self.index, self.paths = index, paths
        self.found = collections.defaultdict(list)
        self.count = collections.Counter()

    def add(self, path, **where):
        self._record(self.paths.forms(path) if self.paths else (_usrmerge(path),), path, where)

    def add_text(self, path, **where):
        self._record((_usrmerge(path),), path, where)

    def _record(self, forms, path, where):
        packages = set()
        for form in forms:
            packages |= self.index.get(form, set())
        for package in sorted(packages):
            self.count[package] += 1
            if len(self.found[package]) < MAX_MATCHES:
                self.found[package].append(dict(where, path=path))


class _Owners:
    """Dono RPM de arquivo citado: ``owned``, ``unowned``, ``failed`` ou ``over_limit``; uma consulta por caminho."""

    def __init__(self, run):
        self.run, self.cache, self.left = run, {}, MAX_OWNER_QUERIES

    def __call__(self, path):
        if path not in self.cache:
            if self.left <= 0:
                return "over_limit"
            self.left -= 1
            names, reason = _owner_lookup(path, self.run)
            self.cache[path] = "owned" if names else "unowned" if reason == "unowned_binary" else "failed"
        return self.cache[path]


def _verdict(package, matches, reasons):
    """``used`` com arquivo do pacote achado; senão ``not_measured`` com motivo; senão ``not_used``."""
    if matches.count[package]:
        return {"state": USED, "reasons": [], "matches": matches.found[package],
                "match_count": matches.count[package]}
    if reasons:
        return {"state": NOT_MEASURED, "reasons": sorted(reasons), "matches": [], "match_count": 0}
    return {"state": NOT_USED, "reasons": [], "matches": [], "match_count": 0}


def _activation_entries(text):
    """Chaves de ``[D-BUS Service]``; ``None`` sem a seção ou sem ``Name`` válido."""
    section, values = None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
        elif section == "D-BUS Service" and "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values if BUS_NAME_RE.match(values.get("Name", "")) else None


def _acquired_names(run):
    """Nomes do barramento de sistema com dono agora; ``None`` quando a lista não foi lida inteira."""
    listed = run(["busctl", "--system", "list", "--acquired", "--no-legend", "--no-pager"])
    if listed["rc"] != 0 or listed["truncated"]:
        return None
    names = set()
    for line in listed["stdout"].splitlines():
        parts = line.split()
        if not parts or parts[0].startswith(":") or "(activatable)" in parts:
            continue
        if not BUS_NAME_RE.match(parts[0]):
            return None
        names.add(parts[0])
    # O próprio org.freedesktop.DBus sempre tem dono: lista vazia é leitura que falhou.
    return names or None


def _systemd_activation(unit, run):
    """Unidade que o D-Bus ativa: ``used``, ``not_activatable``, ``on_demand`` ou o motivo de não medir."""
    if not SERVICE_RE.match(unit):
        return "unit_name_unsupported"
    shown = run(["systemctl", "show", "--no-pager",
                 "--property=LoadState,ActiveState,InactiveExitTimestampMonotonic", "--", unit])
    if shown["rc"] != 0 or shown["truncated"]:
        return "systemd_service_unavailable"
    props = dict(line.split("=", 1) for line in shown["stdout"].splitlines() if "=" in line)
    if props.get("LoadState") in ("not-found", "masked"):
        return "not_activatable"
    exited = props.get("InactiveExitTimestampMonotonic", "")
    if props.get("LoadState") != "loaded" or not exited.isdigit() or "ActiveState" not in props:
        return "systemd_service_unavailable"
    if props["ActiveState"] not in ("inactive", "failed") or int(exited) > 0:
        return USED
    return "on_demand"


def _dbus_of(files, acquired, fs, run):
    """D-Bus de um pacote pelos arquivos de ativação que ele instala."""
    system = sorted(path for path in files
                    if _usrmerge(path).startswith(DBUS_SYSTEM_DIRS) and path.endswith(".service"))
    session = sorted(path for path in files
                     if _usrmerge(path).startswith(DBUS_SESSION_DIRS) and path.endswith(".service"))
    names, reasons, used = [], set(), []
    for path in system:
        try:
            text, cut = fs.read(path, FILE_LIMIT)
        except OSError:
            reasons.add("activation_file_unreadable")
            continue
        entries = None if cut else _activation_entries(text)
        if entries is None:
            reasons.add("activation_file_unsupported")
            continue
        unit = entries.get("SystemdService")
        if unit and not SERVICE_RE.match(unit):
            unit, activation = None, "unit_name_unsupported"  # o recibo só leva nome de unidade válido
        elif unit:
            activation = _systemd_activation(unit, run)
        else:
            program = (entries.get("Exec", "").split() or [""])[0]
            activation = "not_activatable" if program in FALSE_EXECS else "on_demand"
        acquired_now = None if acquired is None else entries["Name"] in acquired
        names.append({"file": path, "name": entries["Name"], "systemd_service": unit,
                      "acquired": acquired_now, "activation": activation})
        if acquired_now or activation == USED:
            used.append({"name": entries["Name"], "via": "acquired" if acquired_now else "activated",
                         "path": path})
            continue
        if acquired is None:
            reasons.add("bus_names_unavailable")
        if activation == "on_demand":
            reasons.add("dbus_activation_on_demand")
        elif activation != "not_activatable":
            reasons.add(activation)
    if session:
        reasons.add("session_bus_not_measured")
    state = USED if used else NOT_MEASURED if reasons else NOT_USED
    return {"state": state, "reasons": sorted(reasons) if state == NOT_MEASURED else [],
            "matches": used[:MAX_MATCHES], "match_count": len(used),
            "activation_files": system + session, "names": names}


def _dbus_dimension(package_files, fs, run):
    """Ativação D-Bus dos pacotes; a lista de nomes com dono só é lida quando algum pacote ativa no sistema."""
    needs_bus = any(_usrmerge(path).startswith(DBUS_SYSTEM_DIRS)
                    for files in package_files.values() for path in files)
    acquired = _acquired_names(run) if needs_bus else set()
    states = {package: _dbus_of(files, acquired, fs, run) for package, files in package_files.items()}
    source = {"status": "measured" if acquired is not None else NOT_MEASURED,
              "reasons": [] if acquired is not None else ["bus_names_unavailable"],
              "source": ["rpm -ql", "busctl --system list --acquired", "systemctl show"],
              "counts": {"acquired_names": len(acquired) if needs_bus and acquired is not None else None,
                         "activation_files": sum(len(state["activation_files"]) for state in states.values())}}
    return source, states


def _strip_deleted(path):
    return path[:-len(DELETED)] if path.endswith(DELETED) else path


class _Cut(Exception):
    """Leitura de processo acima do limite: o processo não foi medido inteiro."""


def _process_kind(pid, fs, identity):
    """(tipo, terminal). ``user`` é medido; ``exited``, ``kernel_thread`` e ``own_session`` ficam fora;
    ``unreadable`` falta. O terminal (``tty_nr``) separa shell interativo de shell lendo um pipe."""
    try:
        text, _ = fs.read("/proc/%s/stat" % pid, 4096)
    except (FileNotFoundError, ProcessLookupError):
        return "exited", 0
    except OSError:
        return "unreadable", 0
    try:
        rest = text.rsplit(")", 1)[1].split()
        state, session, tty, flags = rest[0], int(rest[3]), int(rest[4]), int(rest[6])
    except (IndexError, ValueError):
        return "unreadable", 0
    if flags & PF_KTHREAD:
        return "kernel_thread", tty
    if state in ("Z", "X", "x"):
        return "exited", tty
    if int(pid) == identity["pid"] or session == identity["session"]:
        return "own_session", tty
    return "user", tty


def _process_paths(pid, fs):
    """Executável, argv e o conjunto (via, caminho) do que o processo usa agora."""
    base = "/proc/%s" % pid
    exe = _strip_deleted(fs.readlink(base + "/exe"))
    found = {("exe", exe)}
    maps, cut = fs.read(base + "/maps", MAPS_LIMIT)
    if cut:
        raise _Cut("maps_over_limit")
    for line in maps.splitlines():
        parts = line.split(None, 5)
        if len(parts) == 6 and parts[5].startswith("/"):
            found.add(("maps", _strip_deleted(parts[5].rstrip())))
    descriptors = fs.listdir(base + "/fd")
    if len(descriptors) > MAX_FDS:
        raise _Cut("fds_over_limit")
    for descriptor in descriptors:
        try:
            target = fs.readlink("%s/fd/%s" % (base, descriptor))
        except FileNotFoundError:
            continue  # descritor fechado entre a lista e a leitura
        if target.startswith("/"):
            found.add(("fd", _strip_deleted(target)))
    cmdline, cut = fs.read(base + "/cmdline", FILE_LIMIT)
    if cut:
        raise _Cut("cmdline_over_limit")
    argv = [arg for arg in cmdline.split("\0") if arg]
    found.update(("cmdline", path) for arg in argv for path in _token_paths(arg))
    return exe, argv, found


def _interpreter_named(name):
    """Interpretador (ou shell) pelo nome do executável; ``None`` para programa comum."""
    for spec in INTERPRETERS + (SHELL,):
        if spec.exe.match(name):
            return spec
    return None


def _embedded_languages(found):
    """Linguagens cuja biblioteca o processo mapeia (mod_wsgi, mod_perl, uwsgi): interpretador embutido."""
    names = {path.rsplit("/", 1)[-1] for via, path in found if via == "maps"}
    return {spec.language for spec in INTERPRETERS if any(spec.library.match(name) for name in names)}


def _module_languages(files):
    return {spec.language for spec in INTERPRETERS if any(path.endswith(spec.suffixes) for path in files)}


def _argv_program(spec, argv):
    """O que o interpretador roda, lido da linha de comando só quando a leitura é inequívoca.

    ``("program", caminho)``; ``("none", None)`` sem programa; ``("inline", None)`` com código na linha,
    módulo por nome ou entrada padrão; ``("ambiguous", None)`` com opção que a sonda não conhece. Opção
    curta agrupada (``-Ic``, ``-Im``) é lida letra a letra.
    """
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg == "-":
            return "inline", None
        if not (arg.startswith("-") or (spec is SHELL and arg.startswith("+"))):
            break
        if arg.startswith("--"):
            key = arg.split("=", 1)[0]
            if key in spec.long_with_arg:
                i += 1 if "=" in arg else 2
            elif key in spec.long_no_arg:
                i += 1
            else:
                return "ambiguous", None
            continue
        if len(arg) == 1:
            return "ambiguous", None
        step = 1
        for position, letter in enumerate(arg[1:], 1):
            if letter in spec.inline:
                return "inline", None
            if letter in spec.with_arg:
                step = 2 if position == len(arg) - 1 else 1
                break
            if letter not in spec.no_arg:
                return "ambiguous", None
        i += step
    return ("program", argv[i]) if i < len(argv) else ("none", None)


def _environment_changes_imports(spec, pid, fs):
    """Se o ambiente do processo muda o que ele importa (PYTHONPATH, PERL5LIB, ...). Ilegível conta como sim."""
    try:
        environ, cut = fs.read("/proc/%s/environ" % pid, FILE_LIMIT)
    except OSError:
        return True
    names = {item.split("=", 1)[0] for item in environ.split("\0") if "=" in item}
    return cut or any(name == wanted or name.startswith(wanted + "_") for name in names for wanted in spec.env)


def _interpreter_attributed(spec, argv, pid, tty, fs, paths, owners):
    """``owned`` quando o interpretador roda programa com dono RPM e ambiente sem caminho de import próprio.

    O programa com dono RPM importa o que o RPM requer, e isso o dependente reverso mede. Shell sem programa e
    com terminal é uso manual (limite declarado); qualquer outra leitura não resolvida não atribui.
    """
    kind, program = _argv_program(spec, argv)
    if kind == "none" and spec is SHELL and tty != 0:
        return "owned"
    if kind != "program" or not program.startswith("/") or not fs.is_file(program):
        return "unowned"
    owner = owners(paths.final(program))
    if owner == "owned" and _environment_changes_imports(spec, pid, fs):
        return "unowned"
    return owner


def _proc_restricted(fs, identity):
    """Sem root, ``hidepid`` esconde os processos dos outros usuários: a lista deixa de ser completa."""
    if identity["euid"] == 0:
        return None
    try:
        mounts, cut = fs.read("/proc/mounts", FILE_LIMIT)
    except OSError:
        return "process_list_restricted"
    if cut:
        return "process_list_restricted"
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "/proc" and parts[2] == "proc":
            for option in parts[3].split(","):
                if option.startswith("hidepid=") and option.split("=", 1)[1] not in ("0", "off"):
                    return "process_list_restricted"
    return None


def _processes_dimension(package_files, index, paths, fs, owners, deadline):
    """Processos vivos: o que cada um executa, mapeia, mantém aberto e recebe como argumento.

    Interpretador (executável ou biblioteca embutida) e shell só são atribuídos quando o programa que rodam
    é lido sem ambiguidade e tem dono RPM. Sem isso, a linguagem (ou, no shell, todo pacote) fica sem medida.
    """
    counts, unattributed, reasons = collections.Counter(), collections.Counter(), set()
    matches = _Matches(index, paths)
    judge = _TokenJudge(fs, paths, None)
    languages = set()
    for files in package_files.values():
        languages |= _module_languages(files)
    identity = fs.identity()
    restricted = _proc_restricted(fs, identity)
    if restricted:
        reasons.add(restricted)
    try:
        pids = [name for name in fs.listdir("/proc") if name.isdigit()]
    except OSError:
        pids = []
        reasons.add("process_list_unavailable")
    if len(pids) > MAX_PROCESSES:
        reasons.add("processes_over_limit")
        pids = pids[:MAX_PROCESSES]
    for pid in pids:
        if time.monotonic() > deadline:
            reasons.add("time_budget")
            break
        counts["listed"] += 1
        kind, tty = _process_kind(pid, fs, identity)
        if kind != "user":
            counts[kind] += 1
            continue
        try:
            exe, argv, found = _process_paths(pid, fs)
        except _Cut as cut:
            counts["truncated"] += 1
            reasons.add(str(cut))
            continue
        except (FileNotFoundError, ProcessLookupError):
            counts["exited"] += 1
            continue
        except OSError:
            counts["unreadable"] += 1
            continue
        counts["read"] += 1
        # Argumento que nomeia executável do PATH do próprio processo (ou caminho executável dentro do token) pode ser
        # rodado por ele, então nenhum pacote sai not_used. Os caminhos que ele nomeia continuam como evidência de uso.
        script = _script_argument_index(exe, argv)
        if _inline_code(exe, argv):
            reasons.add("interpreter_inline_code")
        if _process_argument_blocks(judge, argv, pid, fs, script):
            reasons.add("process_argument_unresolved")
        for via, path in sorted(found):
            matches.add(path, pid=int(pid), via=via)
        spec = _interpreter_named(exe.rsplit("/", 1)[-1])
        pending = _embedded_languages(found) - {spec.language if spec else None}
        if spec is not None and (spec is SHELL or spec.language in languages):
            owner = _interpreter_attributed(spec, argv, pid, tty, fs, paths, owners)
            if owner != "owned":
                pending.add(spec.language)
            if owner in ("failed", "over_limit"):
                reasons.add("owner_lookup_failed" if owner == "failed" else "owner_queries_over_limit")
        for language in pending:
            unattributed[language] += 1
    if counts["unreadable"]:
        reasons.add("process_unreadable")
    if unattributed["shell"]:
        reasons.add("shell_process_unattributed")
    states = {}
    for package, files in package_files.items():
        own = {"interpreter_process_unattributed"} if any(
            unattributed[language] for language in _module_languages(files)) else set()
        states[package] = _verdict(package, matches, reasons | own)
    source = {"status": NOT_MEASURED if reasons else "measured", "reasons": sorted(reasons),
              "source": ["/proc/mounts", "/proc/<pid>/stat", "/proc/<pid>/exe", "/proc/<pid>/maps",
                         "/proc/<pid>/fd", "/proc/<pid>/cmdline"],
              "counts": {key: counts[key] for key in (
                  "listed", "read", "unreadable", "truncated", "exited", "kernel_thread", "own_session")},
              "interpreters_unattributed": dict(sorted(unattributed.items()))}
    return source, states


def _script_text(text):
    """Script agendado sem comentário; atribuição fica, porque pode citar arquivo do pacote."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _cron_command(line, kind):
    """Comando de uma linha de crontab, ou ``None`` quando os campos não seguem a gramática.

    ``system``: /etc/crontab e cron.d, com campo de usuário; ``user``: spool; ``ana``: anacrontab.
    """
    if kind == "ana":
        parts = line.split(None, 3)
        valid = len(parts) == 4 and (parts[0] in CRON_KEYWORDS or parts[0].isdigit()) and parts[1].isdigit()
        return parts[3] if valid else None
    user = 1 if kind == "system" else 0
    if line.startswith("@"):
        parts = line.split(None, 1 + user)
        return parts[-1] if len(parts) == 2 + user and parts[0] in CRON_KEYWORDS else None
    parts = line.split(None, 5 + user)
    valid = len(parts) == 6 + user and all(CRON_FIELD_RE.match(field) for field in parts[:5])
    return parts[-1] if valid else None


def _wrapped(name, args):
    """Comando que nice, nohup ou ionice executa, depois das opções que a sonda lê; ``None`` se ambíguo."""
    args = list(args)
    if name == "nice":
        if args[:1] == ["-n"] and len(args) > 1 and re.match(r"^-?[0-9]+$", args[1]):
            args = args[2:]
        elif args and NICE_RE.fullmatch(args[0]):
            args = args[1:]
    elif name == "ionice":
        while args and args[0].startswith("-"):
            if args[0] in ("-c", "-n") and len(args) > 1 and args[1].isdigit():
                args = args[2:]
            elif IONICE_RE.fullmatch(args[0]):
                args = args[1:]
            else:
                return None
    return args if args and not args[0].startswith("-") else None


def _worst(*statuses):
    """Uma parte sem identificação deixa a entrada sem resolução; sem ela, uma parte fora da lista basta."""
    for status in (UNRESOLVED, OFF_LIST):
        if status in statuses:
            return status
    return RESOLVED


def _token_paths(arg):
    """Caminhos que um token nomeia, mesmo fora da lista: cada ``/caminho`` dentro dele, seja puro, depois de ``=``,
    colado a opção (``-I/caminho``) ou separado por qualquer caractere (``:``, ``,``, ``%``...). Servem de evidência
    de uso, e de ``named`` quando o caminho é executável; nunca de ausência."""
    return re.findall(r"/[A-Za-z0-9._+@/-]+", arg)


def _clean_path(text):
    """Caminho absoluto único, só com o conjunto de caracteres admitido, sem ``//`` e sem ``..``."""
    return CLEAN_PATH_RE.fullmatch(text) is not None and "//" not in text and ".." not in text


def _token_shape(arg):
    """Forma do token pelo conjunto de caracteres admitido. A gramática interna decide só o que passa aqui.

    ``RESOLVED``: forma admitida (opção sem barra, ``--opt=`` vazio, caminho absoluto único, ``--opt=`` com caminho
    absoluto único). ``OFF_LIST``: forma que a gramática descreve como fora da lista (``--``, ``-``, ``VAR=valor``,
    opção de login, barra colada a opção sem ``=``, ``--opt=a/b``). ``UNRESOLVED``: o resto, inclusive qualquer
    caractere fora do conjunto (``:``, ``,``, ``;``, ``=`` no caminho, ``$``, glob, ``~``, ``%``, chaves, aspas).
    """
    if OPTION_RE.fullmatch(arg) or EMPTY_VALUE_RE.fullmatch(arg) or _clean_path(arg):
        return RESOLVED
    found = OPTION_VALUE_RE.fullmatch(arg)
    if found is not None:
        value = found.group(1)
        if arg.startswith("--") and value.startswith("/"):
            return RESOLVED if _clean_path(value) else UNRESOLVED
        if "/" in value and not value.startswith("/") and CHARSET_RE.fullmatch(value):
            return OFF_LIST
        return UNRESOLVED
    if arg in ("--", "-") or ENV_ASSIGN_RE.match(arg) or LOGIN_OPTION_RE.match(arg):
        return OFF_LIST
    glued = GLUED_OPTION_PATH_RE.fullmatch(arg)
    if glued is not None and _clean_path(glued.group(1)):
        return OFF_LIST
    return UNRESOLVED


TokenVerdict = collections.namedtuple("TokenVerdict", ("status", "named", "unknown", "data"))


def _token_words(arg):
    """Palavras que um token pode nomear como programa: sufixos de opção curta colada (``-Izstd``), valor de
    ``--opt=`` e token sem barra. Caminho não entra: é checado como arquivo. Opção longa não nomeia nada."""
    if "/" in arg:
        return []
    if OPTION_RE.fullmatch(arg):
        short = SHORT_CLUSTER_RE.fullmatch(arg)
        if short is None:
            return []
        rest = short.group(2)
        return [rest[i:] for i in range(len(rest))]
    if arg.startswith("-"):
        found = OPTION_VALUE_RE.fullmatch(arg)
        return [found.group(1)] if found is not None and found.group(1) else []
    return [arg]


def _process_path_dirs(pid, fs):
    """PATH efetivo de um processo, de ``/proc/<pid>/environ``. Sem a variável, o PATH padrão. ``None`` quando o
    ambiente é ilegível, cortado ou tem entrada relativa (o diretório atual do PATH não se sabe)."""
    try:
        environ, cut = fs.read("/proc/%s/environ" % pid, FILE_LIMIT)
    except OSError:
        return None
    if cut:
        return None
    for item in environ.split("\0"):
        if item.startswith("PATH="):
            entries = item[len("PATH="):].split(":")
            if not all(entry.startswith("/") for entry in entries):
                return None
            return tuple(entry.rstrip("/") or "/" for entry in entries)
    return STANDARD_BIN_DIRS


class _TokenJudge:
    """Avaliador de token único. Agendamento, argumentos de ``#!`` e argv de processo chamam o mesmo ``evaluate``.

    ``owned`` é a checagem de dono do agendamento; com ``None`` (processo vivo) o dono não entra no resultado, porque
    no processo só ``named`` decide. ``evaluate`` devolve ``TokenVerdict``: ``status`` é a classificação de agendamento
    (``RESOLVED``, ``OFF_LIST`` ou ``UNRESOLVED``); ``named`` diz que o token nomeia um executável (sufixo de opção
    curta colada, palavra ou caminho executável dentro do token, no PATH dado); ``unknown`` diz que há palavra que
    poderia nomear programa e o PATH é ``None``; ``data`` é o caminho de dado que conta como uso quando resolve.
    """

    def __init__(self, fs, paths, owned):
        self.fs, self.paths, self.owned = fs, paths, owned

    def evaluate(self, token, path_dirs):
        named, unknown = self._names(token, path_dirs)
        shape = _token_shape(token)
        if shape != RESOLVED:
            return TokenVerdict(shape, named, unknown, None)
        if LOGIN_OPTION_RE.match(token):
            return TokenVerdict(OFF_LIST, named, unknown, None)
        if OPTION_RE.fullmatch(token):
            # Opção curta com restante colado: se um sufixo é programa do PATH, o token não resolve (B14).
            short = SHORT_CLUSTER_RE.fullmatch(token) is not None
            return TokenVerdict(UNRESOLVED if short and named else RESOLVED, named, unknown, None)
        if token.startswith("-"):
            value = OPTION_VALUE_RE.fullmatch(token).group(1)
            if value.startswith("/"):
                return self._data(value, named, unknown)
            return TokenVerdict(RESOLVED, named, unknown, None)
        return self._data(token, named, unknown)

    def _names(self, token, path_dirs):
        """(nomeia executável, não se sabe): caminho executável dentro do token, ou palavra no PATH dado."""
        executable_path = any(self.fs.is_file(path) and self._executable(path) for path in _token_paths(token))
        words = _token_words(token)
        if path_dirs is None:
            return executable_path, bool(words)
        named_word = any(self._in_path(word, path_dirs) for word in words)
        return executable_path or named_word, False

    def _in_path(self, word, path_dirs):
        return any(self.fs.is_file(candidate) and self._executable(candidate)
                   for candidate in ("%s/%s" % (directory, word) for directory in path_dirs))

    def _executable(self, path):
        try:
            mode = self.fs.mode(path)
        except OSError:
            return True  # sem saber, pode ser executado
        return stat.S_ISREG(mode) and bool(mode & 0o111)

    def _data(self, path, named, unknown):
        """Caminho de dado: executável é programa que o executor roda; arquivo sem dono pode ser código; o resto casa."""
        if self.fs.is_file(path):
            if self._executable(path):
                return TokenVerdict(OFF_LIST, named, unknown, None)
            if self.owned is not None and not self.owned(self.paths.final(path)):
                return TokenVerdict(UNRESOLVED, named, unknown, None)
        return TokenVerdict(RESOLVED, named, unknown, path)


def _script_argument_index(exe, argv):
    """Índice do script que um interpretador ou shell lê (``python3 -s script``). Ele é dado para o interpretador, e o
    dono do script já é atribuído pelo caminho de interpretador. ``None`` quando não é interpretador ou não há script."""
    spec = _interpreter_named(exe.rsplit("/", 1)[-1])
    if spec is None or not argv:
        return None
    kind, program = _argv_program(spec, argv)
    return argv.index(program, 1) if kind == "program" and program in argv[1:] else None


# Componente que a sonda sabe julgar: palavra, caminho ou opção do conjunto admitido. Qualquer outro caractere (``$``,
# glob, aspas, parênteses, chaves...) pode virar nome de programa, então o componente não pode ser julgado.
JUDGEABLE_RE = re.compile(r"[A-Za-z0-9._+@/-]+")
# Separadores que dividem um token fora do conjunto admitido em componentes.
COMPONENT_SPLIT_RE = re.compile(r"[:,;%=\s]")
# Código inline: ``-c``, ``-e``, ``-E``, ``--eval`` e ``-r`` seguidos de argumento. Vale também para interpretador
# desconhecido, porque a sonda não sabe o que ele roda.
INLINE_CODE_OPTIONS = frozenset({"-c", "-e", "-E", "--eval", "-r"})


# Prefixo de opção de um componente: ``--nome`` ou uma letra depois de ``-`` (o ``-I`` de ``-I./x``).
OPTION_PREFIX_RE = re.compile(r"--[A-Za-z0-9_-]*|-[A-Za-z0-9]")


def _relative_component(component):
    """Componente com caminho relativo (``./x``, ``../x``, ``bin/x``, ``-I./x``): tem ``/`` depois do prefixo de opção e
    o caminho não começa com ``/``. O diretório atual do processo não se sabe, então o nome pode ser programa."""
    match = OPTION_PREFIX_RE.match(component)
    body = component[match.end():] if match else component
    return "/" in body and not body.startswith("/")


def _process_token_blocks(judge, token, dirs):
    """Um argumento de processo bloqueia ``not_used`` quando nomeia executável do PATH do próprio processo, ou quando não
    pode ser julgado. Token do conjunto admitido (B15) é julgado pelo avaliador. Fora do conjunto, o token é dividido nos
    separadores e cada componente é julgado do mesmo jeito; componente com caractere que a sonda não interpreta bloqueia,
    e componente com caminho relativo também (B19: o diretório atual do processo não se sabe)."""
    if _token_shape(token) == RESOLVED:
        verdict = judge.evaluate(token, dirs)
        return verdict.named or verdict.unknown
    for component in COMPONENT_SPLIT_RE.split(token):
        if not component:
            continue
        if _relative_component(component) or JUDGEABLE_RE.fullmatch(component) is None:
            return True
        verdict = judge.evaluate(component, dirs)
        if verdict.named or verdict.unknown:
            return True
    return False


def _process_argument_blocks(judge, argv, pid, fs, script):
    """Bloqueio de ``not_used`` pelos argumentos do processo. argv[0] (título do processo) e o script que o interpretador
    lê ficam fora. O PATH do processo só é lido quando há argumento."""
    arguments = [arg for index, arg in enumerate(argv[1:], 1) if index != script]
    if not arguments:
        return False
    dirs = _process_path_dirs(pid, fs)
    return any(_process_token_blocks(judge, arg, dirs) for arg in arguments)


def _inline_code(exe, argv):
    """Código inline roda programa que não está no argv. Vale para qualquer interpretador da tabela (``_argv_program``
    lê ``-c``, ``-e``, ``-m`` e equivalentes como código inline) e para interpretador desconhecido com as opções de
    ``INLINE_CODE_OPTIONS`` seguidas de argumento."""
    spec = _interpreter_named(exe.rsplit("/", 1)[-1])
    if spec is not None and argv and _argv_program(spec, argv)[0] == "inline":
        return True
    return any(arg in INLINE_CODE_OPTIONS and index + 1 < len(argv) for index, arg in enumerate(argv) if index)


def _timer_service(unit):
    """Serviço que o timer ativo dispara, como a sonda o leu (pelo modelo quando ``via_model``)."""
    target = unit.get("target")
    if unit.get("status") != "resolved" or not isinstance(target, str):
        return None
    if unit.get("via_model"):
        model = MODEL_RE.match(target) or INSTANCE_RE.match(target)
        return "%s@%s.service" % (model.group(1), MODEL_INSTANCE) if model else None
    return target


class _Schedules:
    """Agendamentos lidos: casa arquivos dos pacotes e classifica cada entrada pela lista fechada do módulo.

    ``RESOLVED`` só quando a forma é admitida; ``UNRESOLVED`` quando algo não se identifica; ``OFF_LIST`` quando
    tudo se identifica mas a forma não é admitida. Só entrada ``RESOLVED`` deixa a dimensão apta a ``not_used``.
    """

    def __init__(self, index, paths, fs, owners, deadline):
        self.matches = _Matches(index, paths)
        self.paths, self.fs, self.owners, self.deadline = paths, fs, owners, deadline
        self.counts = collections.Counter()
        self.reasons = set()
        self.judge = _TokenJudge(fs, paths, self.owned)
        self.script_dirs = {paths.final(directory) for directory in CRON_SCRIPT_DIRS}

    def expired(self):
        if time.monotonic() > self.deadline:
            self.reasons.add("time_budget")
            return True
        return False

    def text(self, path):
        try:
            text, cut = self.fs.read(path, FILE_LIMIT)
        except (FileNotFoundError, NotRegularFile):
            return None
        except OSError:
            self.reasons.add("schedule_source_unreadable")
            return None
        self.counts["files"] += 1
        if cut:
            self.reasons.add("schedule_file_over_limit")
        return text

    def listing(self, directory):
        try:
            names = self.fs.listdir(directory)
        except FileNotFoundError:
            return []
        except OSError:
            self.reasons.add("schedule_source_unreadable")
            return []
        names = [name for name in names if not name.startswith(".") and not name.endswith(SKIPPED_SUFFIXES)]
        if len(names) > MAX_SCHEDULE_FILES:
            self.reasons.add("schedule_files_over_limit")
            names = names[:MAX_SCHEDULE_FILES]
        return ["%s/%s" % (directory, name) for name in names]

    def scan(self, text, source):
        """Só casa (pelo usrmerge): caminho absoluto e comando no PATH padrão. Não resolve nada."""
        for token in TOKEN_RE.findall(text):
            if "/" in token:
                for path in _token_paths(token):
                    self.matches.add_text(path, source=source)
            else:
                for directory in STANDARD_BIN_DIRS:
                    self.matches.add_text("%s/%s" % (directory, token), source=source)

    def owned(self, path):
        status = self.owners(path)
        if status in ("failed", "over_limit"):
            self.reasons.add("owner_lookup_failed" if status == "failed" else "owner_queries_over_limit")
        return status == "owned"

    def outcome(self, status):
        """Conta a entrada sem resolução: ``unresolved``, ou ``not_allowlisted`` quando a forma está fora da lista."""
        if status != RESOLVED:
            self.counts["unresolved" if status == UNRESOLVED else "not_allowlisted"] += 1

    def _program(self, word, path_dirs):
        """Arquivo que a palavra executa, depois do ``realpath``; ``None`` quando não se identifica."""
        if word.startswith("/"):
            return self.paths.final(word) if self.fs.is_file(word) else None
        if "/" in word or path_dirs is None:
            return None
        for directory in path_dirs:
            candidate = "%s/%s" % (directory, word)
            if self.fs.is_file(candidate):
                return self.paths.final(candidate)
        return None

    def resolves(self, words, path_dirs, source, wrapped=False):
        """Entrada na lista fechada do módulo: ``RESOLVED``, ``UNRESOLVED`` ou ``OFF_LIST``, vence o pior item.

        O programa precisa ser identificado e ter dono RPM. PATH próprio, executor de login ou de ambiente e
        comando dentro de comando (só ``nice``, ``nohup`` ou ``ionice``, uma vez) ficam fora da lista. Cada
        argumento passa por ``_argument``.
        """
        if not words:
            return UNRESOLVED
        if ENV_ASSIGN_RE.match(words[0]):
            return OFF_LIST
        if words[0].startswith("/") and not _clean_path(words[0]):
            return UNRESOLVED
        program = self._program(words[0], path_dirs)
        if program is None or not self.owned(program):
            return UNRESOLVED
        self.matches.add(program, source=source)
        name, args = program.rsplit("/", 1)[-1], words[1:]
        form = OFF_LIST if path_dirs is None or name in LOGIN_EXECUTORS else RESOLVED
        if name in EXEC_WRAPPERS:
            inner = _wrapped(name, args)
            if wrapped or inner is None:
                return OFF_LIST
            return _worst(form, self.resolves(inner, path_dirs, source, wrapped=True))
        if name == "run-parts":
            # Só os diretórios cron.* que a sonda lê; diretório próprio roda scripts que ela não vê.
            cron = (len(args) == 1 and args[0].startswith("/")
                    and self.paths.final(args[0].rstrip("/")) in self.script_dirs)
            return _worst(form, RESOLVED if cron else UNRESOLVED)
        if _interpreter_named(name) is not None:
            # Forma admitida: <interpretador> <script absoluto com dono> [argumentos]. Opção ou ambiente antes do
            # script fica fora da lista (``bash -l``, ``sh -i``, ``python3 -u``): o interpretador muda o que roda.
            script = args[0] if args else ""
            if script == "--" or script.startswith("-") or ENV_ASSIGN_RE.match(script):
                return OFF_LIST
            if not script.startswith("/") or not self.fs.is_file(script) or not self.owned(self.paths.final(script)):
                return UNRESOLVED
            self.matches.add(script, source=source)
            args = args[1:]
        else:
            # Programa executado direto: se for script, o interpretador do ``#!`` também precisa se identificar.
            form = _worst(form, self._shebang(program, source))
        return _worst(form, *[self._argument(arg, source, path_dirs) for arg in args])

    def _argument(self, arg, source, path_dirs):
        """Um token de agendamento pelo avaliador de token único (``_TokenJudge``). Caminho de dado que resolve vira
        uso do pacote; a classificação (resolvido, fora da lista ou sem resolução) é a do avaliador."""
        verdict = self.judge.evaluate(arg, path_dirs)
        if verdict.status == RESOLVED and verdict.data is not None:
            self.matches.add(verdict.data, source=source)
        return verdict.status

    def _shebang(self, path, source):
        """Interpretador que o kernel roda para um script com ``#!``. Dono RPM conta como uso do pacote dono.

        ``#!/usr/bin/env X`` segue a regra de palavra: X precisa resolver pelo PATH padrão do cron e ter dono. Arquivo
        sem ``#!`` é binário e não tem interpretador a ler. Cabeçalho ilegível, relativo ou sem dono não se identifica.
        """
        try:
            head, _ = self.fs.read(path, SHEBANG_LIMIT)
        except OSError:
            return UNRESOLVED
        if not head.startswith("#!"):
            return RESOLVED
        words = head.split("\n", 1)[0][2:].split()
        if not words or not _clean_path(words[0]):
            return UNRESOLVED
        interp = self.paths.final(words[0])
        if interp == "/usr/bin/env":
            named = words[1] if len(words) > 1 and not words[1].startswith("-") else ""
            if named.startswith("/") and not _clean_path(named):
                return UNRESOLVED
            program = self._program(named, CRON_DEFAULT_PATH) if named else None
            if program is None or not self.owned(program):
                return UNRESOLVED
            self.matches.add(program, source=source)
            args = words[2:]
        else:
            if not self.fs.is_file(interp) or not self.owned(interp):
                return UNRESOLVED
            self.matches.add(interp, source=source)
            args = words[1:]
        return _worst(*[self._argument(arg, source, CRON_DEFAULT_PATH) for arg in args])

    def command(self, command, path_dirs, source):
        """Linha de comando: separador, expansão e aspas não se identificam; o resto segue ``resolves``."""
        cleaned = SIMPLE_REDIRECT_RE.sub(" ", command)
        if UNRESOLVED_RE.search(cleaned):
            return UNRESOLVED
        return self.resolves(cleaned.split(), path_dirs, source)

    def table(self, path, kind):
        """Um crontab: variáveis na ordem em que aparecem e cada linha de comando pela gramática de ``kind``."""
        text = self.text(path)
        if text is None:
            return
        self.matches.add(path, source=path)
        self.scan(text, path)
        path_dirs, safe = CRON_DEFAULT_PATH, True
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            assignment = ENV_ASSIGN_RE.match(line)
            if assignment:
                name, value = assignment.group(1), assignment.group(2).strip().strip("\"'")
                if name == "PATH":
                    dirs = tuple(directory.rstrip("/") for directory in value.split(":"))
                    path_dirs = dirs if all(directory in STANDARD_BIN_DIRS for directory in dirs) else None
                elif name == "SHELL":
                    safe = safe and value in STANDARD_SHELLS
                elif name not in SAFE_CRON_ENV:
                    safe = False  # variável que o comando pode expandir ou que muda o que ele carrega
                continue
            self.counts["entries"] += 1
            command = _cron_command(line, kind)
            if not safe or command is None:
                self.outcome(UNRESOLVED)
            else:
                self.outcome(self.command(command, path_dirs, path))

    def tables(self):
        for path in CRON_FILES:
            if self.expired():
                return
            self.table(path, "ana" if path.endswith("anacrontab") else "system")
        for directory, kind in zip(CRON_TABLE_DIRS, ("system", "user")):
            for path in self.listing(directory):
                if self.expired():
                    return
                self.table(path, kind)

    def scripts(self):
        for directory in CRON_SCRIPT_DIRS:
            for path in self.listing(directory):
                if self.expired():
                    return
                try:
                    mode = self.fs.mode(path)
                except FileNotFoundError:
                    continue
                except OSError:
                    self.reasons.add("schedule_source_unreadable")
                    continue
                if not stat.S_ISREG(mode) or not mode & 0o111:
                    continue  # run-parts não executa
                self.counts["entries"] += 1
                self.matches.add(path, source=path)
                # Script com dono RPM roda o que o RPM requer; sem dono, pode rodar qualquer coisa. O run-parts executa
                # o script direto: o interpretador do ``#!`` também precisa se identificar.
                owned = RESOLVED if self.owned(self.paths.final(path)) else UNRESOLVED
                self.outcome(_worst(owned, self._shebang(self.paths.final(path), path)))
                text = self.text(path)
                if text is not None:
                    self.scan(_script_text(text), path)

    def at_jobs(self):
        for path in self.listing(AT_SPOOL):
            if self.expired():
                return
            text = self.text(path)
            if text is None:
                continue
            # Job de at é o shell do usuário mais o comando: não há programa identificável.
            self.counts["entries"] += 1
            self.outcome(UNRESOLVED)
            self.scan(text, path)

    def timers(self, units, unit_reasons, run):
        if {"trigger_list_unavailable", "triggers_over_limit"} & set(unit_reasons):
            self.reasons.add("timer_list_incomplete")
        for unit in units:
            if unit.get("kind") != "timer":
                continue
            if self.expired():
                return
            self.counts["timers"] += 1
            name = _timer_service(unit)
            shown = run(["systemctl", "show", "--no-pager", "--property=" + TIMER_SERVICE_PROPERTIES, "--", name]
                        ) if name else None
            if shown is None or shown["rc"] != 0 or shown["truncated"]:
                self.reasons.add("timer_unresolved")
                continue
            source, commands = "timer:" + unit["unit"], 0
            for line in shown["stdout"].splitlines():
                key, _, value = line.partition("=")
                value = value.strip()
                if not value:
                    continue
                if key == "EnvironmentFiles" or (key == "Environment" and RISKY_ENV_RE.search(value)):
                    self.counts["entries"] += 1
                    self.outcome(UNRESOLVED)  # ambiente que a sonda não lê ou que muda o que o programa carrega
                elif key.startswith("Exec"):
                    commands += 1
                    self.timer_command(value, source)
            if not commands:
                self.reasons.add("timer_unresolved")

    def timer_command(self, value, source):
        """Uma linha Exec* do serviço: ``{ path=... ; argv[]=... ; ignore_errors=... ; ... }``, sem shell."""
        self.counts["entries"] += 1
        match = EXEC_LINE_RE.match(value)
        if match is None:
            self.outcome(UNRESOLVED)
            return
        program, argv = match.group(1), match.group(2)
        self.scan(argv, source)
        words = argv.split()
        if UNRESOLVED_RE.search(argv) or not words:
            self.outcome(UNRESOLVED)
            return
        self.outcome(self.resolves([program] + words[1:], SYSTEMD_DEFAULT_PATH, source))


def _schedules_dimension(package_files, index, paths, units, unit_reasons, fs, run, owners, deadline):
    """Agendamentos do sistema e timers ativos que citam arquivo do pacote."""
    schedules = _Schedules(index, paths, fs, owners, deadline)
    schedules.tables()
    schedules.scripts()
    schedules.at_jobs()
    schedules.timers(units, unit_reasons, run)
    reasons = set(schedules.reasons)
    if schedules.counts["unresolved"]:
        reasons.add("schedule_command_unresolved")
    if schedules.counts["not_allowlisted"]:
        reasons.add("schedule_form_not_allowlisted")
    states = {package: _verdict(package, schedules.matches, reasons) for package in package_files}
    source = {"status": NOT_MEASURED if reasons else "measured", "reasons": sorted(reasons),
              "source": list(CRON_FILES + CRON_TABLE_DIRS + CRON_SCRIPT_DIRS) + [AT_SPOOL, "systemctl show"],
              "counts": {key: schedules.counts[key]
                         for key in ("files", "entries", "timers", "unresolved", "not_allowlisted")}}
    return source, states


def _unmeasured_dimension(package_files, reason):
    return ({"status": NOT_MEASURED, "reasons": [reason], "source": [], "counts": {}},
            {package: _verdict(package, _Matches({}), {reason}) for package in package_files})


def _measure(dimension, known, index, paths, units, unit_reasons, fs, run, owners, deadline):
    if dimension == "dbus_activation":
        return _dbus_dimension(known, fs, run)
    if dimension == "processes":
        return _processes_dimension(known, index, paths, fs, owners, deadline)
    if dimension == "schedules":
        return _schedules_dimension(known, index, paths, units, unit_reasons, fs, run, owners, deadline)
    return _unmeasured_dimension(known, "dimension_unsupported")


class _Deadline(Exception):
    """O prazo duro das dimensões estourou: o que faltou medir fica ``not_measured``."""


def _on_alarm(_signum, _frame):
    raise _Deadline()


def _arm_alarm(seconds):
    """Arma o SIGALRM das dimensões; ``None`` fora da thread principal, onde só vale o prazo cooperativo."""
    try:
        handler = signal.signal(signal.SIGALRM, _on_alarm)
    except (ValueError, AttributeError):
        return None
    return handler, signal.alarm(seconds), time.monotonic()


def _disarm_alarm(armed):
    """Desarma e devolve o alarme que já existia (quem chamou a sonda continua com o prazo dele)."""
    if armed is None:
        return
    handler, pending, armed_at = armed
    signal.alarm(0)
    signal.signal(signal.SIGALRM, handler)
    if pending:
        signal.alarm(max(1, int(pending - (time.monotonic() - armed_at))))


def _usage_dimensions(requested, package_files, units, unit_reasons, run, fs):
    """Uso de cada pacote pedido além das unidades. Pacote sem lista de arquivos fica ``not_measured``."""
    started = time.monotonic()
    block = {"requested": list(requested), "sources": {}, "packages": {}}
    if not requested or package_files is None:
        reason = "request_invalid" if not requested else "reverse_request_missing"
        block.update(elapsed_ms=_elapsed_ms(started), coverage={"status": "partial", "reasons": [reason]})
        return block
    known = {package: files for package, files in package_files.items() if files is not None}
    owners = _Owners(run)
    deadline = started + DIMENSION_TIME_BUDGET_S
    measured = {}
    armed = _arm_alarm(DIMENSION_TIME_BUDGET_S + DIMENSION_GRACE_S)
    try:
        paths = _Paths(fs)
        index = paths.index(known)
        for dimension in requested:
            measured[dimension] = _measure(dimension, known, index, paths, units, unit_reasons, fs, run, owners,
                                           deadline)
            measured[dimension][0]["observed_at"] = _now()
    except _Deadline:
        pass  # o que não terminou fica not_measured com time_budget, logo abaixo
    finally:
        _disarm_alarm(armed)
    for dimension in requested:
        source, states = measured.get(dimension) or _unmeasured_dimension(known, "time_budget")
        source.setdefault("observed_at", _now())
        block["sources"][dimension] = source
        for package in package_files:
            block["packages"].setdefault(package, {})[dimension] = states.get(package) or _verdict(
                package, _Matches({}), {"package_files_unavailable"})
    reasons = {reason for source in block["sources"].values() if source["status"] != "measured"
               for reason in source["reasons"]}
    if len(known) != len(package_files):
        reasons.add("package_files_unavailable")
    block.update(elapsed_ms=_elapsed_ms(started),
                 coverage={"status": "partial" if reasons else "complete", "reasons": sorted(reasons)})
    return block


def collect(request, run=command, fs=None):
    if not isinstance(request, dict):
        raise ValueError("binding must be an object")
    started = time.monotonic()
    reasons = []
    listed = run(["systemctl", "list-units", "--type=service", "--state=running",
                  "--no-legend", "--plain", "--no-pager"])
    if listed["rc"] != 0 or listed["truncated"]:
        reasons.append("unit_list_unavailable")
        names = []
    else:
        names = []
        for line in listed["stdout"].splitlines():
            token = line.split()[0] if line.split() else ""
            if UNIT_RE.match(token):
                names.append(token)
            elif token:
                reasons.append("unit_name_unsupported")
    if len(names) > MAX_UNITS:
        reasons.append("units_over_limit")
        names = names[:MAX_UNITS]
    units = []
    for name in names:
        if time.monotonic() - started > TIME_BUDGET_S:
            units.append(_unit(name, "unresolved", "time_budget"))
        else:
            units.append(_inspect_unit(name, run))
    trigger_entries, trigger_reasons = _trigger_units(run, started)
    units.extend(trigger_entries)
    reasons.extend(trigger_reasons)
    for entry in units:
        if entry["status"] != "resolved":
            reasons.append(entry["reason"])
    seed = {package for entry in units for package in entry["packages"]}
    closure = _closure(seed, run, started)
    if closure["truncated"]:
        reasons.append("closure_truncated")
    running, kernel_packages, kernel_unknown, kernel_failed = _kernel(run)
    if kernel_unknown:
        reasons.append("kernel_unknown")
    if kernel_failed:
        reasons.append("kernel_packages_unavailable")
    if not (kernel_unknown or kernel_failed) and not any(
            f"{pkg['version']}-{pkg['release']}" == _release_of(running) for pkg in kernel_packages):
        reasons.append("running_kernel_unmatched")
    unresolved = [entry["unit"] for entry in units if entry["status"] != "resolved"]
    reasons = sorted(set(reasons))
    reverse = _reverse_request(request)
    fs = fs or HostFiles()
    observation = {
        "schema": SCHEMA,
        "binding": request,
        # Privilégio medido pela própria sonda, não declarado pelo launch: o REE confere com o template que lançou.
        "effective_uid": fs.identity()["euid"],
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service_mutations": 0,
        "configuration_writes": 0,
        "elapsed_ms": _elapsed_ms(started),
        "running_kernel": running,
        "kernel_packages": kernel_packages,
        "units": units,
        "closure": closure,
        "coverage": {
            "status": "partial" if reasons or unresolved else "complete",
            "reasons": reasons,
            "unresolved_units": unresolved,
            "scope": _scope(),
        },
    }
    package_files = None
    if reverse is not None:
        observation["reverse_dependencies"], package_files = _reverse_dependencies(reverse, run)
    dimensions = _dimension_request(request)
    if dimensions is not None:
        observation["usage_dimensions"] = _usage_dimensions(
            dimensions, package_files, units, reasons, run, fs)
    return observation


if __name__ == "__main__":
    # Mesmo contrato de ``collect_dependency_linux.py``: o request chega em argv[1] (python -c).
    request = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    print(json.dumps(collect(request), sort_keys=True, separators=(",", ":")))
