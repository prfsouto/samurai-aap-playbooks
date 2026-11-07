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
  (``/etc/crontab``, ``/etc/anacrontab``, ``/etc/cron.*``, ``/var/spool/cron``, ``/var/spool/at``) e, só do pid
  com match, ``/proc/<pid>/cgroup``;
- só com ``successor_runtime`` no request (candidata B): ``rpm -q`` e ``rpm -ql`` dos nomes pedidos,
  ``systemctl show`` das unidades pedidas, ``busctl get-property`` do próximo disparo dos timers pedidos e ``systemctl list-units``/``show`` dos Exec* de todo serviço carregado,
  o ``#!`` e o corpo dos scripts desses programas e dos executáveis dos requerentes, os crontabs do sistema
  (``/etc/crontab``, ``/etc/anacrontab``, ``/etc/cron.d``, ``/etc/cron.*``), o spool de usuário
  (``/var/spool/cron``), os jobs de ``at`` (``/var/spool/at``) e, numa consulta ``rpm -qa --qf`` lida linha a
  linha, os scriptlets de todo pacote instalado.

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
  NetworkManager) fica fora da medição. ``sources.schedules.spools`` diz, para o crontab de usuário e o ``at``, se
  o diretório foi lido por inteiro, quantos arquivos e quais pacotes pedidos alguma entrada cita (caminho ou nome
  no PATH), sem o texto: a N5 usa isso para fechar em B, pela origem, o spool que a coleta sem root não lista.

Gramática declarada por executável (``EXEC_GRAMMARS``): agetty (``-o`` é argumento do login, a menos que ``-l`` troque
o programa), unbound-anchor (valor de opção é dado, exceto ``-C``, que carrega configuração) e dnf (a primeira palavra
posicional é um dos comandos sem transação; aliases de ``/etc/dnf/aliases.d/`` ou arquivo ilegível tiram a resolução).
A gramática cobre o token da linha de comando, não os hooks de plugin do dnf, que são Python carregado por
configuração. Cada entrada exige o pacote dono esperado e cita a semântica da versão no código; o critério completo
está na docstring de ``EXEC_GRAMMARS``. O que a gramática não cobre continua sem resolução.

Na dúvida, ``not_measured``: falha parcial ou entrada que a sonda não resolve nunca vira ausência de uso.

Onde o bloqueio ficou: ``usage_dimensions.sources.processes.blocking`` lista o pid, o executável e as posições
no argv de cada processo que bloqueou (nunca o valor do argumento); ``sources.schedules.unresolved_entries`` lista a
fonte e a linha, ou o timer, de cada entrada agendada sem resolução (nunca o comando), com um ``detail`` fixo
(``environment_file``, ``risky_environment``, ``exec_line_unparsed``, ``argv_unresolved``, ``token_unresolved:<índice
do argv>`` ou ``command_unresolved``), sem texto de comando nem valor de variável. Cada lista tem teto de
50 entradas; o que passa do teto só soma em ``*_over_limit``. Cada match de processo traz ``exe`` e ``exe_owner``
(pacote dono único do executável, ou ``null``) e ``unit`` (unidade systemd do pid em ``/proc/<pid>/cgroup``, ou
``null``).

Dono de cada match de processo e de agendamento: ``path_owner`` (dono RPM único do caminho observado),
``resolved_path`` (o arquivo depois do ``realpath``) e ``resolved_owner`` (dono RPM único dele). ``via_link_of_root``
é ``true`` só quando o caminho é do pacote pedido apenas porque ele é dono de 1 a ``MAX_LINK_PATHS`` links
(``root_link_paths``, todos listados) que resolvem para arquivo de outro pacote, com dono resolvido único (o python36 do
RHEL 8 tem ``/usr/bin/python3`` e ``/usr/bin/python3.6`` para o mesmo arquivo); arquivo do próprio pacote, dono
desconhecido, dono ambíguo ou mais links que o teto é ``false``. Essas consultas têm orçamento próprio
(``MAX_OWNER_QUERIES``) e não mudam nenhum veredito; sem consulta disponível o dono fica ``null`` e ``via_link_of_root``
``false``.

Sucessor em B (``successor_runtime``, só no request da candidata B): por raiz omitida, ``rpm -q`` da raiz e do
sucessor; estado, resultado e Exec* de cada unidade pedida (timer pelo serviço que dispara); dono RPM e ``#!`` dos
programas dessas unidades; executáveis dos requerentes (``rpm -ql`` em ``/usr/bin``, ``/usr/sbin``, ``/usr/libexec``)
com o ``#!``; e as referências à raiz nos Exec* de todo serviço carregado, no ``#!`` e no corpo dos scripts desses
programas, nos crontabs do sistema e de usuário e nos jobs de ``at``. A referência é o caminho (``absolute_path``) ou o
nome sem caminho de um comando da raiz num diretório do PATH (``path_name``), as duas formas de
``coverage.reference_forms``; o caminho citado é normalizado (``//usr/bin/x``, ``/./``, ``..``) antes de comparar.
``undecidable_references`` lista, por fonte, o que a sonda não decide sem avaliar o shell (expansão que cita a raiz
ou expansão na posição de comando de texto de shell que o resolvedor estrito não decide): a N5 conta como
``not_proven``. O resolvedor (``_ShellScript``) decide a variável só por atribuição no próprio texto, arquivo carregado
com ``.``/``source`` de caminho decidido, ``Environment=``/``EnvironmentFile=`` da unidade e variável do crontab; nada
vem do ambiente herdado. Substituição de comando, ``read``, ``eval``, laço sobre entrada externa, parâmetro posicional,
atribuição condicional não literal, arquivo carregado ausente ou ilegível e variável sem atribuição (fora o default
literal de ``${X:-lit}``) ficam indecidíveis, com o motivo, a variável e a origem. O decidido vai para
``resolved_command_variables`` (referências da raiz e a origem e linha de cada atribuição), e valor que cita a raiz é referência. Por medição, com a fonte no recibo, também se decidem: wrapper da lista de permissão pela gramática da página man
(``ionice -p`` só tem IDs; ``timeout 5 cmd`` roda ``cmd``; opção fora da lista é indecidível), glob literal de laço
expandido no host (``glob_expansions`` com o sha256 de cada arquivo, varrido pelo mesmo contrato), ``.`` em ramo morto
pelo teste de existência do mesmo caminho (``dead_source_branches``), ``eval`` de literais (``decided_evals``), ``$N`` de
função pela união das chamadas e array de atribuição literal. Procedência do fornecedor medida, sem tirar nada do estrito: o indecidível em arquivo
de pacote do fornecedor do destino, assinado pela chave da release de B (Key ID de 64 bits da chave importada), de
repositório do fornecedor e sem divergência no ``rpm -V`` vai para ``vendor_code_undecidable`` (arquivo, linha, palavra,
motivo e o registro do fornecedor; a N5 conta como ``not_proven``); ``vendor_trusted`` grava pacote, NEVRA, chave,
``rpm -V``, repositório e release. O que ele carrega, roda ou cita em ``/etc`` fora desse conjunto e o ``%config``
alterado do pacote vão ao estrito (``workload_scanned``); o arquivo que falha uma medida fica no estrito
(``vendor_trust_refused``). ``reference_owners`` diz, por
caminho de referência, se ele existe em B, o dono RPM dele e o do arquivo resolvido (a N5 decide se a referência
quebra). Timer pedido traz ``next_elapse_epoch`` (próximo disparo, lido
por ``busctl get-property`` do systemd) e ``observed_epoch`` (relógio de B). Programa de unidade que não existe na raiz não é ilegível: não cita nada. Os
executáveis do requerente vêm do ``rpm -ql`` lido linha a linha. ``coverage.scanned`` diz o que foi lido e ``coverage.limit`` a fonte que não se leu
inteira (sem permissão de listar o spool, lista de serviços indisponível ou acima de ``MAX_OUTSIDE_UNITS``): busca
parcial não prova ausência global. Binário sem ``#!`` não é interpretado. ``coverage.scriptlets`` diz, dos
scriptlets lidos (o rpmdb se lê sem root), que pacotes podem escrever no spool na instalação (``crontab`` em
qualquer posição como comando, não ``/etc/crontab``; ``at``/``batch`` como comando; caminho do spool que não é
arquivo ponto); consulta não lida inteira deixa
``package_scriptlets`` em ``limit``. ``coverage.package_history`` lê o histórico do dnf (``/var/lib/dnf/history.sqlite``,
0644) só para leitura e diz que pacotes saíram do rpmdb (atualizados, removidos, obsoletos, reinstalados ou
rebaixados): o %preun/%postun deles rodou e não está mais em B. Histórico ausente ou ilegível deixa
``package_history`` em ``limit``. É o que a N5 exige para fechar o spool de B pela origem.

Privilégio da coleta (C1): por padrão o coletor roda sem ``become`` (``ec2-user``). Com isso, ``/proc`` de outro
usuário e o spool de cron podem ficar ilegíveis, e ``processes`` e ``schedules`` só provam ``used``; ``not_used`` não
sai nessas dimensões. Só o template dedicado ``samurai_inventory_source_privileged_probe`` (decisão do dono, D1) roda
a sonda como root, com o request de omissões de A e o ``privileged_measurement`` desta execução. O recibo leva
``effective_uid`` (o euid real da sonda), e o REE recusa recibo com root fora desse template. No código, root só muda uma coisa: com ``euid == 0``, ``_proc_restricted`` não marca a lista como restrita
por ``hidepid``. A regra não muda: qualquer motivo de ``not_measured`` (processo ilegível, corte por limite, entrada sem resolução, código inline de
interpretador, fonte ilegível) continua impedindo ``not_used``. Root não garante ``not_used``; só retira um motivo
de ``not_measured``. A sonda não escolhe o privilégio; ela só o recebe.
"""
import ast
import base64
import collections
import fnmatch
import datetime
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import struct
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

# Primeiro caractere alfanumérico: nome que começa com "-" nunca vira opção de systemctl. O escape ``\xNN`` do
# systemd (``systemd-fsck@dev-disk-by\x2duuid-….service``) é um caractere do nome; o nome vai como argv, sem shell.
_UNIT_CHAR = r"(?:[A-Za-z0-9@_.:-]|\\x[0-9a-f]{2})"
_INSTANCE_CHAR = r"(?:[A-Za-z0-9_.:-]|\\x[0-9a-f]{2})"
UNIT_RE = re.compile(r"^[A-Za-z0-9]" + _UNIT_CHAR + r"{0,127}\.(service|timer|socket)$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9]" + _UNIT_CHAR + r"{0,127}\.service$")
MODEL_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.:-]{0,126})@\.service$")
INSTANCE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.:-]{0,126})@[A-Za-z0-9]" + _INSTANCE_CHAR + r"{0,126}\.service$")
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
MAX_BLOCKING = 50
MAX_UNRESOLVED_ENTRIES = 50
MAX_LINK_PATHS = 4
MAX_SUCCESSOR_CHECKS = 8
MAX_CHECK_ITEMS = 16
MAX_REQUIRER_EXECUTABLES = 32
SUCCESSOR_TIME_BUDGET_S = 90  # 30 s da varredura + os 60 s dos imports Python
SUCCESSOR_UNIT_PROPERTIES = "LoadState,ActiveState,Result,Unit,ExecMainStartTimestampMonotonic,ExecStartPre,ExecStart"
# Fontes além das unidades pedidas que podem citar a raiz em B; a que não se lê inteira fica em ``coverage.limit``.
SUCCESSOR_SOURCES = ("at_jobs", "package_history", "package_scriptlets", "programs_outside_requested_units",
                     "user_crontab_spool")
MAX_OUTSIDE_UNITS = 512
OUTSIDE_BATCH = 16
OUTSIDE_EXEC_PROPERTIES = "Environment,EnvironmentFiles,ExecCondition,ExecStartPre,ExecStart,ExecStartPost,ExecReload,ExecStop,ExecStopPost"
# Scriptlets de todo pacote instalado em B, numa consulta só ao rpmdb (legível sem root), lidos linha a linha.
SCRIPTLET_MARK = "@@samurai-package "
SCRIPTLET_QUERY = ["rpm", "-qa", "--qf", SCRIPTLET_MARK + "%{NAME}\\n"
                   "%|PRETRANS?{%{PRETRANS}\\n}|%|PREIN?{%{PREIN}\\n}|%|POSTIN?{%{POSTIN}\\n}|"
                   "%|PREUN?{%{PREUN}\\n}|%|POSTUN?{%{POSTUN}\\n}|%|POSTTRANS?{%{POSTTRANS}\\n}|"
                   "%|VERIFYSCRIPT?{%{VERIFYSCRIPT}\\n}|[%{TRIGGERSCRIPTS}\\n][%{FILETRIGGERSCRIPTS}\\n]"
                   "[%{TRANSFILETRIGGERSCRIPTS}\\n]"]
# Histórico do dnf (0644, legível sem root): pacote que saiu do rpmdb no build rodou %preun/%postun que B não tem.
DNF_HISTORY = "/var/lib/dnf/history.sqlite"
# Ações do libdnf (TransactionItemAction) em que a versão antiga sai do rpmdb: DOWNGRADED, OBSOLETED, UPGRADED,
# REMOVE e REINSTALLED.
LEFT_RPMDB_ACTIONS = (3, 5, 7, 8, 10)
MAX_HISTORY_NAMES = 64
SCRIPTLET_BYTES = 33554432
SCRIPTLET_TIMEOUT_S = 20
# Palavra que escreve no spool: ``crontab`` em qualquer posição; ``at``/``batch`` como comando de um segmento.
SCRIPTLET_SEGMENT_RE = re.compile(r"[;&|()`'\"\n]|\$\(")
# Caminho do spool citado no scriptlet; o grupo 2 diz o arquivo dentro dele (ponto = não é job).
SPOOL_PATH_RE = re.compile(r"/var/spool/(cron|at)(?=$|[/\s;&|()`'\"<>])(/[^\s;&|()`'\"<>]*)?")
# Quem executa o comando que vem depois (com opções e valores no meio) e palavra de controle do shell.
SCRIPTLET_RUNNERS = frozenset({"sudo", "su", "runuser", "nohup", "nice", "ionice", "env", "exec", "command", "xargs",
                               "timeout", "stdbuf", "time", "chroot", "setsid", "flock"})
SCRIPTLET_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "in", "!",
                                "{", "}", "case", "esac"})
EXECUTABLE_PREFIXES = ("/usr/bin/", "/usr/sbin/", "/usr/libexec/")
# Biblioteca do requerente que o carregador abre: ELF direto na pasta de bibliotecas. Cada DT_NEEDED dela precisa
# existir em B; a sonda lê os bytes e resolve o soname como o ``ld.so`` (RPATH/RUNPATH, ``/etc/ld.so.conf``, pasta
# padrão da classe), sem executar nada.
LIBRARY_RE = re.compile(r"^/usr/lib(?:64)?/[^/]+\.so(?:\.[0-9]+)*$")
# Módulo Python do requerente: só se conta. O ``sys.path`` com ``site`` não se mede sem rodar código do host.
PYTHON_LIBRARY_RE = re.compile(r"^/usr/lib(?:64)?/python3[.0-9]*/site-packages/.+\.py$")
MAX_REQUIRER_LIBRARIES = 32
LIBRARY_READ_LIMIT = 64 << 20
LD_SO_CONF = "/etc/ld.so.conf"
MAX_LD_SO_CONF_FILES = 64
LOADER_DEFAULT_DIRS = {1: ("/usr/lib",), 2: ("/usr/lib64",)}  # classe ELF -> pasta padrão do x86 (32 e 64 bits)
# Multicall do coreutils-single: cada comando é um script ``#!/usr/bin/coreutils --coreutils-prog-shebang=<nome>``.
COREUTILS_SHEBANG = "--coreutils-prog-shebang="
COREUTILS_PACKAGES = frozenset({"coreutils-single", "coreutils"})
USEC_INFINITY = 18446744073709551615
# Shell cujo texto a sonda lê por posição de comando; quem executa outro comando ou carrega outro texto.
SHELL_NAMES = frozenset({"sh", "bash", "dash", "ksh", "zsh"})
CITE_RUNNERS = SCRIPTLET_RUNNERS | frozenset({"eval", ".", "source"})
# Exec* de serviço pelo D-Bus do systemd (``a(sasbttttuii)``: caminho, argv, ...): o argv vem por elemento, sem a
# ambiguidade do texto de ``systemctl show`` (``;`` e espaço dentro de aspas).
EXEC_DBUS_PROPERTIES = ("ExecCondition", "ExecStartPre", "ExecStart", "ExecStartPost", "ExecReload", "ExecStop",
                        "ExecStopPost")
REDACTED = "\u2039redigido\u203a"
# O recibo conserva estrutura, variáveis e referências exatas, sem literais livres.
WORD_PART_REDACT_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?$!-]|\$\(|`|\)|\s+|[\"']")
BRACED_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?:\[(?:[0-9]+|[@*])\])?"
BRACED_DEFAULT_RE = re.compile(r"^\$\{(" + BRACED_NAME + r")(:?[-=+?])(.+)\}$")
BRACED_REFERENCE_RE = re.compile(r"\$\{(?:" + BRACED_NAME + r"|[0-9@*#?$!-])\}")
SAFE_RECEIPT_COMMANDS = CITE_RUNNERS | SHELL_NAMES | frozenset({"curl"})
# Separador de comando do shell (sem as aspas, que não separam comando) e a substituição de comando.
CITE_SEPARATOR_RE = re.compile(r"[;&|()\n`]|\$\(")
COMMAND_SUBSTITUTION_RE = re.compile(r"\$\((?!\()|`")
# Trecho entre aspas simples (literal) ou duplas (com escape), na ordem em que aparecem.
QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", re.S)
SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
# Resolvedor estrito de variáveis (decisão do orquestrador, 29/10): profundidade e tamanho da união fixos.
SHELL_RESOLVE_DEPTH = 4
MAX_SHELL_VALUES = 32
MAX_RESOLVED_ENTRIES = 50
SHELL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHELL_SEPARATORS = frozenset({";", "&", "|", "(", ")", "\n", "`", "$("})
GLOB_RE = re.compile(r"[*?\[]")
BLOCK_OPENERS = frozenset({"if", "case", "for", "while", "until", "select", "{"})
BLOCK_CLOSERS = frozenset({"fi", "esac", "done", "}"})
CODE_PRELUDE = frozenset({"set", "export", "readonly", "declare", "typeset", "local", ".", "source", "shopt", "trap",
                          "umask", ":"})
SEGMENT_SPLIT_RE = re.compile(r"([;&|()\n`]|\$\()")
FUNCTION_DEF_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_:.-]*)\s*\(\s*\)")
# ``[[ $X ]] || X=v`` e ``[ -n "$X" ] || X=v``: depois da linha, X nunca é vazio.
GUARD_RE = re.compile(r"^\s*\[\[?\s+(?:-n\s+)?\$?\x01?\$?\{?([A-Za-z_][A-Za-z0-9_]*)\}?\x01?\s+\]\]?\s*\|\|\s*\1=")
HEREDOC_RE = re.compile(r"<<(-?)\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
QUOTE_SLOT_RE = re.compile(r"\$?\x01(\d+)\x01")
WORD_PART_RE = re.compile(r"(\\.)|('[^']*')|(\"(?:[^\"\\]|\\.)*\")|([^'\"\\]+)")
VAR_REF_RE = re.compile(r"\$(?:\{([^}]*)\}|([A-Za-z_][A-Za-z0-9_]*)|([0-9@*#?$!-]))")
DEFAULT_OP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?::?-(.*))?$")
# Declaração do shell: a palavra seguinte é atribuição ou nome, nunca comando.
SHELL_DECLARATIONS = frozenset({"local", "export", "readonly", "declare", "typeset"})
ARITHMETIC_RE = re.compile(r"\$\(\([^()]*\)\)")
# ``]]`` que fecha é palavra própria: o ``]]`` de ``[[:space:]]`` dentro do teste não fecha.
DOUBLE_BRACKET_RE = re.compile(r"\[\[\s.*?\s\]\](?=\s|$|[;&|)])")
BUS_LABEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
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
# Crontabs do sistema lidos pelo sucessor em B; o spool de usuário e o ``at`` são lidos à parte, quando há permissão.
SYSTEM_CRON_DIRS = ("/etc/cron.d",) + CRON_SCRIPT_DIRS
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
# Gramática declarada por executável: o argumento listado não pode fazer o processo executar um programa, e a
# gramática cobre o token da linha de comando, não os hooks de plugin do dnf (Python, carregados por configuração).
# agetty (util-linux 2.32.1, term-utils/agetty.c): o único exec é ``execv(options.login)``; só
# ``-l``/``--login-program`` troca esse programa; ``-o``/``--login-options`` vai para os argumentos do login (agetty.8).
# unbound-anchor (unbound 1.16.2, a versão do spec c8s, smallapp/unbound-anchor.c): nenhum exec, system, popen ou fork;
# ``-C`` carrega unbound.conf na libunbound e fica excluído; as demais opções com valor são arquivo, porta, endereço ou
# texto de dados (unbound-anchor.8).
# dnf-3 (dnf 4.7.0): a primeira palavra posicional é buscada em ``cli_commands`` (cli.py, ``_parse_commands``) e nunca
# vira programa; a lista abaixo tem só comandos sem transação (confirmados em cli.py e nos módulos). ``-c``/``--config``
# e ``--setopt`` carregam configuração e ficam excluídos. Se ``/etc/dnf/aliases.d/`` define a palavra, ou não pode ser
# lido por inteiro, a palavra não resolve (aliases.py troca a primeira palavra e ignora arquivo ilegível).
ExecGrammar = collections.namedtuple("ExecGrammar", (
    "package", "value_short", "optional_short", "payload_short", "excluded_short", "value_long", "optional_long",
    "payload_long", "excluded_long", "program_short", "program_long", "commands", "alias_dir"))
# ``namedtuple(defaults=...)`` só existe a partir do 3.7; o guest RHEL 8 roda a sonda no platform-python 3.6.
ExecGrammar.__new__.__defaults__ = (frozenset(),) * 11 + (None,)
_NO_NAMES = frozenset()
# Só comandos do dnf 4.7.0 sem transação de pacote: consulta, metadata e solver em memória (check usa Goal sem
# do_transaction). Install, update, remove, erase, autoremove, downgrade, distro-sync, upgrade, swap, localinstall,
# reinstall, shell/sh, group, module e history ficam fora: rodam transação com scriptlet.
DNF_COMMANDS = frozenset({
    "makecache", "check-update", "list", "info", "search", "provides", "repolist", "repoquery", "updateinfo",
    "deplist", "check", "help"})
AGETTY_LONG_VALUE = frozenset({"autologin", "chdir", "delay", "issue-file", "host", "init-string", "login-program",
                               "login-options", "nice", "chroot", "timeout", "erase-chars", "kill-chars"})
EXEC_GRAMMARS = {
    "agetty": ExecGrammar("util-linux", value_short=frozenset("aCdfHIloPrt"), optional_short=frozenset("L"),
                          payload_short=frozenset("o"), value_long=AGETTY_LONG_VALUE,
                          optional_long=frozenset({"local-line"}), payload_long=frozenset({"login-options"}),
                          program_short=frozenset("l"), program_long=frozenset({"login-program"})),
    "unbound-anchor": ExecGrammar("unbound-libs", value_short=frozenset("CPabcfnrsux"),
                                  payload_short=frozenset("Pabcfnrsux"), excluded_short=frozenset("C")),
    # ``/usr/bin/dnf-3`` vem do ``python3-dnf`` (dnf.spec c8s 4.7.0, ``%files -n python3-%{name}``); ``dnf`` só tem o link.
    "dnf-3": ExecGrammar("python3-dnf", value_short=frozenset("c"), excluded_short=frozenset("c"),
                         value_long=frozenset({"config", "setopt"}), excluded_long=frozenset({"config", "setopt"}),
                         commands=DNF_COMMANDS, alias_dir="/etc/dnf/aliases.d"),
}
"""Critério (bedin): a entrada só existe quando a semântica da versão confirma que o token não faz o processo executar
programa. Ficam de fora: opções que carregam módulo ou configuração interpretada (``excluded_*``), subcomandos que
rodam transação com scriptlet (fora de ``DNF_COMMANDS``) e palavras que aliases ou configuração possam trocar
(``alias_dir``). Entrada que a gramática não cobre continua sem resolução."""


def stream_lines(argv, on_line, max_bytes=None, timeout_s=None):
    """Executa sem shell e entrega cada linha do stdout; acima de ``max_bytes`` ou do prazo, corta e marca ``truncated``.

    O ``timeout`` do coreutils mata o comando no prazo: a leitura termina mesmo se ele parar de escrever.
    """
    max_bytes = SCRIPTLET_BYTES if max_bytes is None else max_bytes
    timeout_s = SCRIPTLET_TIMEOUT_S if timeout_s is None else timeout_s
    receipt = {"rc": None, "truncated": False}
    process = None
    try:
        process = subprocess.Popen(["timeout", "--signal=KILL", str(timeout_s), *argv], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"},
                                   start_new_session=True)
        size = 0
        for raw in process.stdout:
            size += len(raw)
            if size > max_bytes:
                _kill_group(process)
                receipt["truncated"] = True
                break
            on_line(raw.decode("utf-8", "replace").rstrip("\n"))
        process.wait(timeout=JOIN_TIMEOUT_S)
        receipt["rc"] = process.returncode
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            _kill_group(process)
        receipt["truncated"] = True
    return receipt


def read_package_history(path=None):
    """Pacotes que saíram do rpmdb em qualquer transação do dnf, lidos do histórico só para leitura.

    Sem o arquivo, sem o módulo ``sqlite3`` ou com erro de leitura, ``complete`` fica falso: a ausência do histórico
    não prova que nenhum pacote saiu.
    """
    path = path or DNF_HISTORY
    result = {"complete": False, "transactions": 0, "left_rpmdb": []}
    try:
        import sqlite3
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except Exception:  # noqa: BLE001 — sem módulo ou sem arquivo legível: leitura incompleta
        return result
    try:
        transactions = connection.execute("SELECT count(*) FROM trans").fetchone()[0]
        names = sorted({row[0] for row in connection.execute(
            "SELECT DISTINCT rpm.name FROM trans_item JOIN rpm ON rpm.item_id = trans_item.item_id "
            "WHERE trans_item.action IN (%s)" % ",".join(str(action) for action in LEFT_RPMDB_ACTIONS))})
    except Exception:  # noqa: BLE001 — esquema diferente ou banco travado: leitura incompleta
        return result
    finally:
        connection.close()
    result.update(complete=transactions > 0 and len(names) <= MAX_HISTORY_NAMES, transactions=transactions,
                  left_rpmdb=names[:MAX_HISTORY_NAMES])
    return result


def _scriptlet_spools(line):
    """Spools que uma linha de scriptlet pode escrever: ``crontab`` em qualquer posição, ``at``/``batch`` como comando
    do segmento (depois de palavra de controle e de atribuição) ou em qualquer ponto depois de quem executa outro
    comando (``sudo``, ``nice``, ``env``...), e os caminhos do spool. Aspas também separam (``su -c "at now"``).

    O comando é a palavra solta ou o caminho num diretório do PATH (``/usr/bin/crontab``); outro caminho com o mesmo
    nome final (``/etc/crontab``, o crontab do sistema) não é o comando. Arquivo ponto do spool (``.SEQ`` do ``at``)
    não é job: os leitores do spool também o ignoram."""
    line = line.strip()
    if not line or line.startswith("#"):
        return set()
    found = set()
    line = re.sub(r"/{2,}", "/", line)
    for match in SPOOL_PATH_RE.finditer(line):
        if not (match.group(2) or "").lstrip("/").startswith("."):
            found.add("user_crontab_spool" if match.group(1) == "cron" else "at_jobs")
    for segment in SCRIPTLET_SEGMENT_RE.split(line):
        words = [_command_name(word) for word in segment.split()]
        if "crontab" in words:
            found.add("user_crontab_spool")
        while words and (words[0] in SCRIPTLET_KEYWORDS or "=" in words[0]):
            words = words[1:]
        # Depois de quem executa outro comando, qualquer ``at``/``batch`` do segmento conta (erro para o lado seguro).
        if words and (words[0] in ("at", "batch") or (
                words[0] in SCRIPTLET_RUNNERS and any(word in ("at", "batch") for word in words[1:]))):
            found.add("at_jobs")
    return found


def _normal_path(path):
    """Caminho na forma comparável: barras repetidas viram uma e ``.``/``..`` são resolvidos pelo texto."""
    collapsed = re.sub(r"/{2,}", "/", path)
    return os.path.normpath(collapsed) if collapsed.startswith("/") else collapsed


def _shell_text(text):
    """Texto lido como shell: sem ``#!`` (crontab, job de ``at``, Exec*) ou com ``#!`` de um shell conhecido."""
    first = text.split("\n", 1)[0]
    if not first.startswith("#!"):
        return True
    words = first[2:].split()
    if not words:
        return True
    name = words[0].rsplit("/", 1)[-1]
    if name == "env" and len(words) > 1:
        name = words[1].rsplit("/", 1)[-1]
    return name in SHELL_NAMES


# Gramática dos wrappers por lista de permissão: as opções documentadas na página man da versão do pacote em B
# (util-linux 2.37.4, coreutils 8.32; ``exec`` pelo ``help exec`` do bash 5.1). Opção fora da lista: indecidível.
# ``end``: opção que não executa comando (IDs, ajuda); ``operands``: palavras antes do comando; ``shell``: o valor é
# texto para ``sh -c``; ``program``: o valor é o programa executado; ``user``: ``runuser -u`` (o resto é o comando).
_HELP = frozenset({"help", "version"})
WRAPPER_GRAMMARS = {
    "ionice": {"flags": "t", "args": "cn", "long_flags": {"ignore"}, "long_args": {"class", "classdata"},
               "end": "pPuhV", "long_end": {"pid", "pgid", "uid"} | _HELP, "source": "util-linux ionice(1)"},
    "nice": {"args": "n", "long_args": {"adjustment"}, "long_end": _HELP, "source": "coreutils nice(1)"},
    "nohup": {"long_end": _HELP, "source": "coreutils nohup(1)"},
    "timeout": {"flags": "v", "args": "ks", "long_flags": {"preserve-status", "foreground", "verbose"},
                "long_args": {"kill-after", "signal"}, "long_end": _HELP, "operands": 1,
                "source": "coreutils timeout(1)"},
    "flock": {"flags": "sexnoFu", "args": "wE", "long_flags": {"shared", "exclusive", "unlock", "nb", "nonblock",
                                                              "close", "no-fork", "verbose"},
               "long_args": {"wait", "timeout", "conflict-exit-code"}, "end": "hV", "long_end": _HELP, "operands": 1,
               "after_operand_shell": ("-c", "--command"), "source": "util-linux flock(1)"},
    "exec": {"flags": "cl", "args": "a", "source": "bash help exec"},
    "env": {"flags": "i0v", "args": "uC", "long_flags": {"ignore-environment", "null", "debug",
                                                         "list-signal-handling"},
            "long_args": {"unset", "chdir"}, "long_optional": {"block-signal", "default-signal", "ignore-signal"},
            "refuse": "S", "long_refuse": {"split-string"}, "long_end": _HELP, "assignments": True, "dash": True,
            "source": "coreutils env(1)"},
    "chrt": {"flags": "ofrbidRav", "args": "TPD", "long_flags": {"other", "fifo", "rr", "batch", "idle", "deadline",
                                                                  "reset-on-fork", "all-tasks", "verbose"},
             "long_args": {"sched-runtime", "sched-period", "sched-deadline"}, "end": "pmVh",
             "long_end": {"pid", "max"} | _HELP, "operands": 1, "source": "util-linux chrt(1)"},
    "taskset": {"flags": "ac", "long_flags": {"all-tasks", "cpu-list"}, "end": "pVh", "long_end": {"pid"} | _HELP,
                "operands": 1, "source": "util-linux taskset(1)"},
    "stdbuf": {"args": "ioe", "long_args": {"input", "output", "error"}, "long_end": _HELP,
               "source": "coreutils stdbuf(1)"},
    "setsid": {"flags": "cfw", "long_flags": {"ctty", "fork", "wait"}, "end": "Vh", "long_end": _HELP,
               "source": "util-linux setsid(1)"},
    "su": {"flags": "fmpPl", "args": "gGw", "shell": "c", "program": "s",
           "long_flags": {"fast", "preserve-environment", "pty", "login"},
           "long_args": {"group", "supp-group", "whitelist-environment"}, "long_shell": {"command", "session-command"},
           "long_program": {"shell"}, "end": "Vh", "long_end": _HELP, "permute": True, "dash": True,
           "source": "util-linux su(1)"},
    "runuser": {"flags": "fmpPl", "args": "gGw", "shell": "c", "program": "s", "user": "u",
                "long_flags": {"fast", "preserve-environment", "pty", "login"},
                "long_args": {"group", "supp-group", "whitelist-environment"},
                "long_shell": {"command", "session-command"}, "long_program": {"shell"}, "long_user": {"user"},
                "end": "Vh", "long_end": _HELP, "permute": True, "dash": True, "source": "util-linux runuser(1)"},
}
# Redirecionamento (``2>/dev/null``, ``15>"$f"``, ``> f``): sai da lista de palavras antes da gramática.
REDIRECT_RE = re.compile(r"^(?:[0-9]+|&)?(?:>>|>&|<&|<>|>\||>|<)(.*)$", re.S)
ARRAY_ASSIGN_RE = re.compile(r"(^|[\s;&|])([A-Za-z_][A-Za-z0-9_]*)(\+?)=\(([^()]*)\)")
ARRAY_SLOT_RE = re.compile(r"\x04(\d+)\x04")
# Caminho absoluto de programa: padrão de ``case`` (``/dev/disk/by-uuid/*)``), glob e resto de expansão não são.
EXECUTED_PATH_RE = re.compile(r"(?:/[\w.+@%,=-]+)+")
CASE_TRIGGER_RE = re.compile(r"\bcase\b[^;]*?\bin\b|;;&?|;&|\besac\b")
CASE_ARM_RE = re.compile(r"\s*(\(?[^()`;]*\))")
REDIRECT_AMP_RE = re.compile(r"(?<=[<>])&|&(?=>)")
BRACKET_SLOT_RE = re.compile(r"\x06(\d+)\x06")
ARRAY_OPEN_RE = re.compile(r"(?:^|[\s;&|])([A-Za-z_][A-Za-z0-9_]*)\+?=\(")
POSITIONAL_HEAD_RE = re.compile(r'^"?\$\{?[1-9]\}?"?$')
ARRAY_ALL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[[@*]\]$")
# Teste de existência de um caminho literal: ``[ -f X ]``, ``[ -e X ]``, ``test -f X``, ``[[ -f X ]]``.
EXISTENCE_TEST_RE = re.compile(r"^(?:\[\[|\[|test)\s+-([efx])\s+(/[A-Za-z0-9._+@/-]+)\s*(?:\]\]|\])?\s*$")
GLOB_NAME_RE = re.compile(r"^[A-Za-z0-9._+@:,=%-]+$")


# ── Import de script lido por interpretador (R7-1 do bedin). ──
# Python: imports pelo ``ast`` resolvidos contra o ``sys.path`` medido no interpretador real; teto fixo de profundidade
# e de módulos. Perl, Ruby e Node: só a detecção de import (indecidível quando há).
PY_IMPORT_MAX_DEPTH = 12
PY_IMPORT_MAX_MODULES = 4000
PY_IMPORT_TIME_BUDGET_S = 60
PY_MODULE_LIMIT = 1 << 20
PY_PATH_FLAGS = "EIsS"
PY_MEASURE_CODE = (
    "import sys,_imp;"
    "print(repr({'path':list(sys.path),'builtin':list(sys.builtin_module_names),"
    "'suffixes':list(_imp.extension_suffixes()),"
    "'frozen':[n for n in ('zipimport','_frozen_importlib','_frozen_importlib_external') if _imp.is_frozen(n)],"
    "'base_installation':sys.prefix==sys.base_prefix and sys.exec_prefix==sys.base_exec_prefix,"
    "'isolated_startup':bool(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)}))"
)
PY_CONTEXT_FIELDS = frozenset({"path", "builtin", "suffixes", "frozen", "base_installation", "isolated_startup"})
PY_CONTEXT_MAX_BYTES = 32768
PY_ENVIRONMENT = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONNOUSERSITE",
                  "PYTHONSAFEPATH", "PYTHONPLATLIBDIR")
PY_STARTUP_ENVIRONMENT = ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "__PYVENV_LAUNCHER__")
FOREIGN_IMPORT_RE = {
    "perl": re.compile(r"(?m)(^|[;{}\s])(use|require|do)\s+[A-Za-z_'\"$:]|\b(require|do)\s*\("),
    "ruby": re.compile(r"(?m)(^|[;\s])(require|require_relative|load|autoload)\b"),
    "node": re.compile(r"(?m)\brequire\s*\(|^\s*import\b|\bimport\s*\("),
}
FOREIGN_COMMENT_RE = {
    "perl": re.compile(r"(?m)#.*$|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""),
    "ruby": re.compile(r"(?m)#.*$|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""),
    "node": re.compile(r"(?m)//.*$|/\*.*?\*/|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`", re.S),
}


def _python_flags(options):
    """Opções que afetam paths, sem aceitar opções de startup com argumentos não provados."""
    letters = []
    for option in options:
        if option.startswith("--") or not option.startswith("-") or any(letter in option[1:] for letter in "WX"):
            return None
        letters.extend(letter for letter in option[1:] if letter in PY_PATH_FLAGS and letter not in letters)
    return "".join(letters)


def _foreign_imports(language, text):
    """Script de Perl, Ruby ou Node com ``use``/``require``/``do FILE``/``load``/``import``/``require()`` fora de
    comentário e de string: a sonda não resolve esses imports."""
    pattern = FOREIGN_IMPORT_RE.get(language)
    if pattern is None:
        return False
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#!"))
    return pattern.search(FOREIGN_COMMENT_RE[language].sub(" ", body)) is not None


def _ast_literal(node):
    """Texto literal de um nó (``ast.Constant`` no Python novo, ``ast.Str`` no 3.6); ``None`` para o resto."""
    constant = getattr(ast, "Constant", None)
    if constant is not None and isinstance(node, constant) and isinstance(getattr(node, "value", None), str):
        return node.value
    legacy = getattr(ast, "Str", None) if sys.version_info < (3, 8) else None
    if legacy is not None and isinstance(node, legacy):
        return node.s
    return None


def _python_imports(text):
    """``(imports, indecidíveis, __all__)`` do módulo. ``imports``: ``(módulo, nível, nomes)``; indecidível:
    ``(motivo, palavra, linha)``; ``__all__``: lista literal, ``None`` quando não é literal, ``[]`` sem ele."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None, [("python_module_unparsed", "", None)], []
    imports, undecided, exported = [], [], []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if isinstance(node, ast.Import):
            imports.extend((alias.name, 0, (), line) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.module or "", node.level or 0, tuple(alias.name for alias in node.names), line))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in ("exec", "eval") and isinstance(func, ast.Name):
                undecided.append(("python_dynamic_code", name, line))
            elif name in ("__import__", "import_module"):
                literal = _ast_literal(node.args[0]) if node.args else None
                if literal is None or literal.startswith("."):
                    undecided.append(("python_dynamic_import", name, line))
                else:
                    imports.append((literal, 0, (), line))
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__"
                                                  for target in node.targets):
            values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else None
            names = [_ast_literal(item) for item in values] if values is not None else None
            exported = names if names is not None and None not in names else None
    return imports, undecided, exported


def _case_patterns_out(line, state):
    """Apaga o padrão do braço do ``case`` (``/dev/disk/by-uuid/*)``), que não é comando: só na posição que a
    gramática espera um padrão (depois de ``case … in`` e de ``;;``/``;&``), até o ``esac``."""
    index = 0
    while True:
        if state["expect"] and line[index:].strip():
            arm = CASE_ARM_RE.match(line, index)
            if arm and not line[index:].lstrip().startswith("esac") and arm.group(1).strip("() "):
                # Vira separador: o comando do braço na mesma linha (``in a) cmd``) segue sendo comando.
                line = line[:arm.start(1)] + ";" + " " * (len(arm.group(1)) - 1) + line[arm.end():]
                index = arm.end()
            state["expect"] = False
        trigger = CASE_TRIGGER_RE.search(line, index)
        if trigger is None:
            return line
        token = trigger.group(0)
        if token == "esac":
            state["depth"], state["expect"] = max(0, state["depth"] - 1), False
        elif token.startswith("case"):
            state["depth"], state["expect"] = state["depth"] + 1, True
        elif state["depth"]:
            state["expect"] = True
        index = trigger.end()


def _receipt_root_token(token, keep):
    if token in keep:
        return token
    if token.startswith("/"):
        reference = _usrmerge(_normal_path(token))
        if reference in keep:
            return reference
    return None


def _redact_word(word, keep=()):
    """Conserva estrutura do shell, nomes conhecidos e referências exatas à raiz."""
    if not word:
        return word
    parts, position, command = [], 0, True
    for match in WORD_PART_REDACT_RE.finditer(word):
        if match.start() > position:
            parts.append(("literal", word[position:match.start()]))
        parts.append(("keep", match.group(0)))
        position = match.end()
    if position < len(word):
        parts.append(("literal", word[position:]))
    out = []
    for kind, text in parts:
        if kind == "keep":
            default = BRACED_DEFAULT_RE.match(text)
            if default is not None:
                out.append("${%s%s%s}" % (default.group(1), default.group(2), _redact_word(default.group(3), keep)))
            elif text.startswith("${") and BRACED_REFERENCE_RE.fullmatch(text) is None:
                out.append("${%s}" % REDACTED)
            else:
                out.append(text)
            if text in ("$(", "`"):
                command = True
            elif not text.isspace():
                command = False
            continue
        reference = _receipt_root_token(text, keep)
        if reference is not None or (command and text in SAFE_RECEIPT_COMMANDS):
            out.append(reference or text)
        elif not (out and out[-1].endswith(REDACTED)) and not (
                len(out) > 1 and out[-1].isspace() and out[-2].endswith(REDACTED)):
            out.append(REDACTED)
        elif out and out[-1].isspace():
            out.pop()
        command = False
    return "".join(out)


def _undecided_entry(source, detail, word, origin=None, line=None):
    return {"source": source, "reason": "expansion_in_command_position", "detail": detail, "variable": None,
            "origin": origin, "line": line, "word": (word or REDACTED)[:120]}


class _Undecided(Exception):
    """Valor que o contrato não decide: o motivo (código fixo), a variável e onde o motivo está (arquivo e linha)."""

    def __init__(self, reason, variable=None, origin=None, origin_line=None, wrapper=None):
        Exception.__init__(self, reason)
        self.reason, self.variable, self.origin = reason, variable, origin
        self.origin_line, self.wrapper = origin_line, wrapper


class _Assignment:
    __slots__ = ("raw", "conditional", "function", "local", "origin", "line", "origin_line", "glob", "loop")

    def __init__(self, raw, conditional, function, local, origin, line, glob=None, loop=None):
        self.raw, self.conditional, self.function = raw, conditional, function
        # ``line`` ordena no texto que carrega; ``origin_line`` é a linha no arquivo ``origin`` (vai para o recibo).
        self.local, self.origin, self.line, self.origin_line = local, origin, line, line
        self.glob = glob  # padrão do laço que deu o valor (arquivo expandido no host)
        self.loop = loop  # laço ``for`` deste texto que deu o valor


class _ShellScript:
    """Texto de shell lido sem executar nada: atribuições, ``.``/``source``, laços, ``read``/``eval`` e as palavras na
    posição de comando. Decide o valor de uma variável só pelas fontes do contrato: atribuição no próprio texto, arquivo
    carregado cujo caminho também se decide, ``Environment=``/``EnvironmentFile=`` da unidade (``env``) e as variáveis do
    crontab (atribuições do próprio texto). Nada vem do ambiente herdado do chamador. Por medição no host também se
    decidem: glob literal de laço (arquivos expandidos), ``.`` em ramo morto pelo teste de existência do mesmo caminho,
    ``eval`` de literais, parâmetro posicional de função pelas chamadas e array de atribuição literal."""

    def __init__(self, text, origin, env, fs, depth=0):
        self.origin, self.env, self.fs, self.depth = origin, env, fs, depth
        self.masked, self.balanced, self.quoted, failed_at = _mask_quotes_keep(text)
        # Aspa sem par: a palavra do recibo é o trecho em que ela começa, com a linha.
        self.unbalanced = None if self.balanced or failed_at is None else (
            text.count("\n", 0, failed_at) + 1, text[failed_at:failed_at + 120].split("\n", 1)[0])
        self.substitution_words = []  # substituição de comando no lugar do comando: linha, palavra, arquivo e linha
        self.assignments = collections.defaultdict(list)
        self.unresolvable = {}
        self.guards = set()
        self.failure, self.failure_origin, self.failure_line = None, None, None
        self.has_eval, self.eval_origin, self.eval_line = False, None, None
        self.first_code = None
        self.words = []
        self.evals = []
        self.arrays, self.brackets = [], []
        self.empty_loops = set()
        self.loops = []  # ``for`` deste texto: variável, função, linha do ``for`` e do ``done``
        self.globs = []          # laços com glob literal: padrão, origem, linha e arquivos expandidos
        self.dead_sources = []   # ``.`` em ramo morto: o caminho testado não existe no host
        self.dead_commands = []  # comando executado em ramo morto do mesmo caminho testado (ausente no host)
        self.decided_evals = []  # ``eval`` cujo argumento é um conjunto finito de literais
        self.loaded = []         # arquivos carregados com ``.``/``source`` (caminho, origem, linha)
        self.decided_paths = set()  # caminhos absolutos que um valor decidido roda na posição de comando
        self.substitutions = []  # linhas com substituição de comando no lugar do comando (``$(f) x``)
        self._calls_cache, self._positional_active = {}, set()
        self._parse()
        if depth == 0:
            self._settle_evals()

    def _restore(self, word):
        return QUOTE_SLOT_RE.sub(lambda match: self.quoted[int(match.group(1))], word)

    def _arrays_out(self, line):
        def slot(match):
            self.arrays.append((match.group(4).split(), bool(match.group(3))))
            return "%s%s=\x04%d\x04" % (match.group(1), match.group(2), len(self.arrays) - 1)
        line = ARRAY_ASSIGN_RE.sub(slot, line)
        for found in ARRAY_OPEN_RE.finditer(line):
            # Array que não é lista literal numa linha (substituição, várias linhas): indecidível.
            after = line[found.end():]
            self.unresolvable[found.group(1)] = "command_substitution" if after.startswith(("$(", "`")) or (
                after.lstrip().startswith(("$(", "`"))) else "array_expression"
        return line

    def _parse(self):
        depth, function, function_depth = 0, None, None
        ifs = []  # um item por ``if`` aberto: [caminho do teste de existência ou None, ramo]
        open_loops = []  # ``for`` abertos: (laço, profundidade depois do ``for``)
        case_state = {"depth": 0, "expect": False}
        for number, line in enumerate(self.masked.splitlines(), 1):
            line = self._arrays_out(line)
            match = FUNCTION_DEF_RE.match(line)
            defining = None
            if match:
                function, function_depth = match.group(1), depth
                defining = match.group(1)  # ``f() {``: o nome antes de ``()`` não é chamada
            if GUARD_RE.match(line):
                self.guards.add(GUARD_RE.match(line).group(1))
            separator, previous, backtick, run, inline = None, "", False, "", None
            # O teste ``[[ ... ]]`` vira uma palavra só (``\x06n\x06``), sem comando dentro; o texto fica para o teste
            # de existência.
            # ``&`` de redirecionamento (``>&``, ``&>``, ``<&``) não separa comando: ``>&/dev/null`` não vira comando.
            line = REDIRECT_AMP_RE.sub("\x07", line)
            line = _case_patterns_out(line, case_state)
            pieces = SEGMENT_SPLIT_RE.split(DOUBLE_BRACKET_RE.sub(self._bracket_slot, line))
            for position, piece in enumerate(pieces):
                if piece in SHELL_SEPARATORS:
                    opening = piece == "$(" or (piece == "`" and not backtick)
                    if piece == "`":
                        backtick = not backtick
                    if opening and not _command_words(previous) and not previous.rstrip().endswith("="):
                        self.substitutions.append(number)
                        inner = pieces[position + 1].strip() if position + 1 < len(pieces) else ""
                        word = self._restore(piece + inner + (")" if piece == "$(" else "`"))[:120]
                        self.substitution_words.append({"line": number, "word": word, "origin": self.origin,
                                                        "origin_line": number})
                    separator, previous, run = piece, "", run + piece
                    continue
                previous = piece
                words = [word.replace("\x07", "&") for word in piece.split()]
                if not words:
                    continue
                if defining and words in ([defining], ["function", defining]):
                    defining = None
                    continue
                joined, run = run, ""
                guard_inline = inline if joined == "&&" else None
                inline = None
                following = pieces[position + 1] if position + 1 < len(pieces) else None
                conditional = depth > 0 or function is not None or separator in ("&", "|", "$(", "`")
                index, skip = 0, False
                while index < len(words) and words[index] in SCRIPTLET_KEYWORDS:
                    keyword = words[index]
                    if keyword in BLOCK_OPENERS:
                        depth += 1
                    elif keyword in BLOCK_CLOSERS:
                        depth = max(0, depth - 1)
                        if function is not None and depth <= function_depth:
                            function, function_depth = None, None
                        if keyword == "done" and open_loops and open_loops[-1][1] == depth + 1:
                            open_loops.pop()[0]["end"] = number
                    if keyword == "if":
                        ifs.append([self._existence_test(words[index + 1:]), "test"])
                    elif keyword == "elif" and ifs:
                        ifs[-1] = [self._existence_test(words[index + 1:]), "test"]
                    elif keyword == "then" and ifs:
                        ifs[-1][1] = "then"
                    elif keyword == "else" and ifs:
                        ifs[-1] = [None, "else"]
                    elif keyword == "fi" and ifs:
                        ifs.pop()
                    if keyword == "for" and index + 1 < len(words):
                        loop = self._loop(words[index + 1:], number, function)
                        if loop is not None:
                            open_loops.append((loop, depth))
                        skip = True
                    if keyword == "case":
                        skip = True  # a palavra do case e o padrão não são comando
                    if skip:
                        break
                    index += 1
                rest = words[index:]
                if skip or not rest:
                    continue
                inline = self._existence_test(rest)
                guarded = {path for path, branch in ifs if path and branch == "then"}
                if guard_inline:
                    guarded.add(guard_inline)
                self._segment(rest, number, function, conditional or depth > 0, depth, separator,
                              substitution=following in ("$(", "`") and piece.rstrip().endswith("="),
                              guarded=guarded)

    def _bracket_slot(self, found):
        self.brackets.append(found.group(0))
        return " \x06%d\x06 " % (len(self.brackets) - 1)

    def _existence_test(self, words):
        """Caminho literal de ``[ -f X ]``/``[ -e X ]``/``test -f X``/``[[ -f X ]]``; ``None`` para outro teste."""
        slot = BRACKET_SLOT_RE.fullmatch(words[0]) if len(words) == 1 else None
        if slot:
            words = self.brackets[int(slot.group(1))].split()
        if any("\x01" in word or "$" in word or "`" in word for word in words):
            return None
        match = EXISTENCE_TEST_RE.match(" ".join(words).strip())
        return _normal_path(match.group(2)) if match else None

    def _loop(self, words, number, function):
        name = words[0]
        values = words[2:] if len(words) > 2 and words[1] == "in" else []
        if not SHELL_NAME_RE.match(name):
            return
        if not values or any("$" in word or "`" in word or "\x01" in word for word in values):
            self.unresolvable[name] = "loop_over_external_input"
            return None
        loop = {"name": name, "function": function, "start": number, "end": None, "id": len(self.loops)}
        assignments = []
        for word in values:
            if not GLOB_RE.search(word):
                assignments.append(_Assignment(word, True, function, False, self.origin, number, loop=loop["id"]))
                continue
            matches = self._expand_glob(word, number)
            if matches is None:
                self.unresolvable[name] = "loop_over_external_input"
                return None
            assignments.extend(_Assignment(path, True, function, False, self.origin, number, glob=word,
                                           loop=loop["id"]) for path in matches)
        if not assignments:
            self.empty_loops.add(name)
        self.assignments[name].extend(assignments)
        self.loops.append(loop)
        return loop

    def _expand_glob(self, pattern, number):
        """Glob literal sobre o filesystem do host (``*``/``?`` só no último componente, diretório absoluto). Volta a
        lista ordenada dos arquivos, ``[]`` quando nada casa e ``None`` quando o padrão não se mede."""
        directory, _, last = pattern.rpartition("/")
        record = {"origin": self.origin, "line": number, "pattern": pattern, "files": [], "status": "expanded"}
        self.globs.append(record)
        if not directory.startswith("/") or GLOB_RE.search(directory) or "[" in last or not last:
            record["status"] = "pattern_unsupported"
            return None
        try:
            names = self.fs.listdir(directory)
        except FileNotFoundError:
            names = []
        except OSError:
            record["status"] = "directory_unreadable"
            return None
        matches = []
        for name in sorted(names):
            if name.startswith(".") and not last.startswith("."):
                continue
            if fnmatch.fnmatchcase(name, last):
                if not GLOB_NAME_RE.match(name):
                    record["status"] = "file_name_unsupported"
                    return None
                matches.append(directory + "/" + name)
        if len(matches) > MAX_SHELL_VALUES:
            record["status"] = "too_many_files"
            return None
        record["files"] = [{"path": path} for path in matches]
        return matches

    def _segment(self, words, number, function, conditional, depth, separator, substitution=False, guarded=()):
        if words[0] in SHELL_DECLARATIONS:
            local = words[0] == "local"
            for position, word in enumerate(words[1:], 1):
                if word.startswith("-"):
                    continue
                name, equal, value = word.partition("=")
                if substitution and position == len(words) - 1 and equal and not value:
                    value = "$("  # ``local x=$(cmd)``: o valor é a saída do comando
                if SHELL_NAME_RE.match(name):
                    self.assignments[name].append(_Assignment(value if equal else "", conditional, function, local,
                                                              self.origin, number))
            return
        index, temporary = 0, len(words) > 1 and not all(SHELL_ASSIGNMENT_RE.match(word) for word in words)
        while index < len(words) and SHELL_ASSIGNMENT_RE.match(words[index]):
            name, _, value = words[index].partition("=")
            name = name.split("[", 1)[0].rstrip("+")
            if substitution and index == len(words) - 1 and not value:
                value = "$("  # ``x=$(cmd)``/``x=`cmd```: o valor é a saída do comando
            self.assignments[name].append(_Assignment(value, conditional or temporary, function, False,
                                                      self.origin, number))
            index += 1
        command = words[index:]
        if not command:
            return
        head = self._restore(command[0]).strip("\"'")
        if head == "read":
            for word in command[1:]:
                if SHELL_NAME_RE.match(word):
                    self.unresolvable[word] = "read"
        elif head == "eval":
            self.has_eval = True
            if self.eval_origin is None:
                self.eval_origin, self.eval_line = self.origin, number
            self.evals.append((number, function, command[1:], conditional, self.origin, number))
        elif head in (".", "source") and len(command) > 1:
            self._source(command[1], number, function, conditional, guarded)
        if self.first_code is None and function is None and depth == 0 and head not in CODE_PRELUDE:
            self.first_code = number
        if head.startswith("/") and _normal_path(head) in guarded and self._absent(_normal_path(head)):
            # Ramo morto: o teste de existência do mesmo caminho literal (ausente no host) não deixa o comando rodar (R-ramo).
            self.dead_commands.append({"origin": self.origin, "line": number, "path": _normal_path(head)})
            return
        self.words.append((number, function, command, separator, self.origin, number))

    def _absent(self, path):
        """Caminho ausente no host medido: a leitura dá ``FileNotFoundError``. Erro de leitura não conta como ausência."""
        try:
            self.fs.read(path, 1)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False

    def _source(self, word, number, function, conditional, guarded=()):
        """Arquivo carregado: o caminho se decide pelo mesmo contrato e o texto entra no ponto do ``.``. Em ramo do
        teste de existência do mesmo caminho literal, caminho ausente no host deixa o ramo morto (nada carrega)."""
        if self.depth >= SHELL_RESOLVE_DEPTH:
            self._fail("depth", self.origin, number)
            return
        try:
            paths = self.evaluate(word, number, function, self.depth + 1)
        except _Undecided as exc:
            self._fail("sourced_path_" + exc.reason, self.origin, number)
            return
        for path in sorted(paths):
            if not path.startswith("/"):
                self._fail("sourced_file_relative", self.origin, number)
                continue
            path = _normal_path(path)
            try:
                text, cut = self.fs.read(path, FILE_LIMIT)
            except FileNotFoundError:
                if path in guarded:
                    self.dead_sources.append({"origin": self.origin, "line": number, "path": path})
                    continue
                self._fail("sourced_file_unreadable", self.origin, number)
                continue
            except OSError:
                self._fail("sourced_file_unreadable", self.origin, number)
                continue
            if cut:
                self._fail("sourced_file_over_limit", path, number)
                continue
            inner = _ShellScript(_script_text(text), path, self.env, self.fs, self.depth + 1)
            if not inner.balanced:
                self._fail("shell_quotes_unbalanced", path, number)
            self.loaded.append({"path": path, "origin": self.origin, "line": number})
            self._merge(inner, number, function, conditional)

    def _merge(self, inner, number, function, conditional):
        """Texto carregado (``.`` ou ``eval`` decidido) entra no ponto da carga, com a própria tabela de aspas."""
        if inner.failure:
            self._fail(inner.failure, inner.failure_origin, inner.failure_line)
        if inner.has_eval and not self.has_eval:
            self.has_eval, self.eval_origin, self.eval_line = True, inner.eval_origin, inner.eval_line
        if inner.substitutions:
            self.substitutions.append(number)
        self.substitution_words.extend(dict(item, line=number) for item in inner.substitution_words)
        offset, array_offset = len(self.quoted), len(self.arrays)
        self.quoted.extend(inner.quoted)

        def shift(raw):  # trecho entre aspas e array do texto carregado vão para as tabelas deste texto
            raw = QUOTE_SLOT_RE.sub(lambda match: match.group(0).replace(
                match.group(1), str(int(match.group(1)) + offset)), raw)
            return ARRAY_SLOT_RE.sub(lambda match: "\x04%d\x04" % (int(match.group(1)) + array_offset), raw)
        self.arrays.extend(([shift(word) for word in words], append) for words, append in inner.arrays)
        self.guards |= inner.guards
        self.empty_loops |= inner.empty_loops
        self.globs.extend(inner.globs)
        self.dead_sources.extend(inner.dead_sources)
        self.dead_commands.extend(inner.dead_commands)
        self.decided_evals.extend(inner.decided_evals)
        self.loaded.extend(inner.loaded)
        for name, reason in inner.unresolvable.items():
            self.unresolvable.setdefault(name, reason)
        for name, assignments in inner.assignments.items():
            for item in assignments:
                item.loop = None
                item.conditional = item.conditional or conditional or function is not None
                item.function = item.function or function
                item.line = number
                if item.glob is None:
                    item.raw = shift(item.raw)
                self.assignments[name].append(item)
        for entry in inner.words:
            self.words.append((number, entry[1] or function, [shift(word) for word in entry[2]], entry[3],
                               entry[4], entry[5]))
        for entry in inner.evals:
            self.evals.append((number, entry[1] or function, [shift(word) for word in entry[2]],
                               entry[3] or conditional, entry[4], entry[5]))

    def _settle_evals(self):
        """``eval`` decide só quando o argumento é um conjunto finito de literais (variável no argumento continua
        indecidível: o próprio ``eval`` pode mudá-la). Decididos todos, cada literal é lido como comando no ponto do
        ``eval``; um indecidível mantém o texto inteiro indecidível, com a origem e a linha dele."""
        if not self.evals or self.failure:
            return
        decided = []
        for number, function, args, conditional, origin, origin_line in self.evals:
            try:
                texts = {""}
                for raw in args:
                    texts = {(left + " " + right).strip() for left in texts
                             for right in self.evaluate(raw, number, function, 1)}
                    if len(texts) > MAX_SHELL_VALUES:
                        raise _Undecided("too_many_values")
            except _Undecided:
                self.eval_origin, self.eval_line = origin, origin_line
                return
            decided.append((number, function, conditional, origin, origin_line, sorted(texts)))
        self.has_eval, self.eval_origin, self.eval_line = False, None, None
        for number, function, conditional, origin, origin_line, texts in decided:
            self.decided_evals.append({"origin": origin, "line": origin_line, "texts": texts[:MAX_CHECK_ITEMS]})
            for text in texts:
                inner = _ShellScript(text, origin, self.env, self.fs, self.depth + 1)
                inner.words = [entry[:4] + (origin, origin_line) for entry in inner.words]
                self._merge(inner, number, function, conditional or len(texts) > 1)

    def _fail(self, reason, origin, line=None):
        if self.failure is None:
            self.failure, self.failure_origin, self.failure_line = reason, origin, line

    def _initial(self, name):
        """Valor antes de qualquer atribuição: o da unidade (``env``) ou vazio; ``env`` ``None`` não foi lido."""
        if self.env is None:
            raise _Undecided("unit_environment_not_read", name)
        return set(self.env.get(name) or {""})

    def _poisoned(self, name):
        if self.failure:
            raise _Undecided(self.failure, name, self.failure_origin, self.failure_line)
        if self.has_eval:
            raise _Undecided("eval", name, self.eval_origin, self.eval_line)

    def resolve(self, name, number, function, depth):
        if depth > SHELL_RESOLVE_DEPTH:
            raise _Undecided("depth", name)
        self._poisoned(name)
        if name in self.unresolvable:
            raise _Undecided(self.unresolvable[name], name, self.origin)
        assignments = list(self.assignments.get(name, ()))
        for loop in self.loops:
            if (loop["name"] == name and loop["function"] == function and loop["end"] is not None
                    and loop["start"] <= number <= loop["end"]):
                assignments = [item for item in assignments if item.loop == loop["id"] or (
                    item.loop is None and item.function == function and loop["start"] < item.line <= loop["end"])]
                return self._values(name, assignments, set(), depth), assignments
        if not assignments:
            if name in self.empty_loops:
                return set(), []
            if self.env is None:
                raise _Undecided("unit_environment_not_read", name, self.origin)
            if name in self.env:
                return set(self.env[name]), []
            raise _Undecided("variable_without_assignment", name)
        scoped = function is not None and any(item.local and item.function == function for item in assignments)
        if scoped:
            assignments = [item for item in assignments if item.function == function]
            values = set()
        else:
            first = self.first_code if self.first_code is not None else 10 ** 9
            killed = any(not item.conditional and item.function is None
                         and (item.line < first or (function is None and item.line < number))
                         for item in assignments)
            if killed:
                values = set()
            elif name in self.guards:
                values = {value for value in self._initial(name) if value}
            else:
                values = self._initial(name)
        return self._values(name, assignments, values, depth), assignments

    def _values(self, name, assignments, values, depth):
        for item in assignments:
            if item.glob is not None:
                values.add(item.raw)
                continue
            if item.conditional and not self._literal(item.raw):
                substituted = "$(" in self._restore(item.raw) or "`" in self._restore(item.raw)
                raise _Undecided("command_substitution" if substituted else "conditional_non_literal", name)
            values |= self.evaluate(item.raw, item.line, item.function, depth + 1)
            if len(values) > MAX_SHELL_VALUES:
                raise _Undecided("too_many_values", name)
        return values

    def _literal(self, raw):
        text = self._restore(ARRAY_SLOT_RE.sub(lambda match: " ".join(self.arrays[int(match.group(1))][0]), raw))
        stripped = re.sub(r"'[^']*'", "", text)
        return "$" not in stripped and "`" not in stripped

    def evaluate(self, raw, number, function, depth):
        """Valores possíveis de uma palavra de shell (aspas, ``$X``, ``${X}``, ``${X:-lit}``, ``${X[@]}``, array
        literal); o resto é indecidível."""
        if depth > SHELL_RESOLVE_DEPTH:
            raise _Undecided("depth")
        array = ARRAY_SLOT_RE.fullmatch(raw)
        if array:
            elements, append = self.arrays[int(array.group(1))]
            if append:
                raise _Undecided("array_append")
            values = {""}
            for element in elements:
                values = {(left + " " + right).strip() for left in values
                          for right in self.evaluate(element, number, function, depth + 1)}
            return values
        if "\x04" in raw:
            raise _Undecided("array_expression")
        text = self._restore(raw)
        values = {""}
        for literal, quoted_single, quoted_double, bare in WORD_PART_RE.findall(text):
            if literal:
                part = {literal[1:]}
            elif quoted_single:
                part = {quoted_single[1:-1]}
            elif quoted_double:
                part = self._expand(quoted_double[1:-1], number, function, depth)
            else:
                part = self._expand(bare, number, function, depth)
            values = {left + right for left in values for right in part}
            if len(values) > MAX_SHELL_VALUES:
                raise _Undecided("too_many_values")
        return values

    def _expand(self, text, number, function, depth):
        if "`" in text or "$(" in ARITHMETIC_RE.sub("", text):
            raise _Undecided("command_substitution")
        text = ARITHMETIC_RE.sub("0", text).replace("\\$", "\x02")
        values, position = {""}, 0
        for match in VAR_REF_RE.finditer(text):
            literal = text[position:match.start()].replace("\\", "").replace("\x02", "$")
            part = self._reference(match, number, function, depth)
            values = {left + literal + right for left in values for right in part}
            position = match.end()
        tail = text[position:].replace("\\", "")
        return {(value + tail).replace("\x02", "$") for value in values}

    def _reference(self, match, number, function, depth):
        braced, plain, special = match.groups()
        if braced is not None and re.match(r"^[1-9]$", braced):
            special = braced
        if special:
            if special in "123456789" and function is not None:
                return self._positional(function, int(special), depth)
            if special in "0123456789@*#":
                raise _Undecided("positional_parameter")
            return {"0"}
        expression = braced if braced is not None else plain
        array = ARRAY_ALL_RE.match(expression)
        if array:
            values, _ = self.resolve(array.group(1), number, function, depth)
            return values
        found = DEFAULT_OP_RE.match(expression)
        if not found:
            raise _Undecided("expansion_operator")
        name, default = found.group(1), found.group(2)
        if default is not None and ("$" in default or "`" in default):
            raise _Undecided("expansion_operator", name)
        try:
            values, assignments = self.resolve(name, number, function, depth)
        except _Undecided as exc:
            if default is None or exc.reason != "variable_without_assignment":
                raise
            return {default, ""}
        if any(ARRAY_SLOT_RE.fullmatch(item.raw) for item in assignments):
            raise _Undecided("array_scalar_reference", name)
        if default is not None:
            values = {value if value else default for value in values}
        return values

    def _positional(self, function, index, depth):
        """``$N`` dentro de função: a união do argumento N de TODAS as chamadas da função no texto alcançável. Chamada
        que a sonda não enumera (comando por variável indecidível) ou argumento que divide em palavras: indecidível."""
        self._poisoned(None)
        key = (function, index)
        if key in self._positional_active or depth > SHELL_RESOLVE_DEPTH:
            raise _Undecided("positional_parameter")
        self._positional_active.add(key)
        try:
            values = set()
            for number, caller, args in self._calls(function):
                args = [word for word in args if not self._is_redirect(word)]
                for word in args[:index]:
                    if self._unquoted_expansion(word) or GLOB_RE.search(QUOTE_SLOT_RE.sub("", word)):
                        raise _Undecided("positional_argument_split")
                values |= {""} if len(args) < index else self.evaluate(args[index - 1], number, caller, depth + 1)
                if len(values) > MAX_SHELL_VALUES:
                    raise _Undecided("too_many_values")
            if any(value.split()[:1] == [function] for value in values):
                raise _Undecided("positional_parameter_call_unknown")
            return values
        finally:
            self._positional_active.discard(key)

    def _calls(self, function):
        cached = self._calls_cache.get(function)
        if isinstance(cached, _Undecided):
            raise cached
        if cached is not None:
            return cached
        calls = []
        try:
            for number, caller, command, _separator, _origin, _line in self.words:
                words = [word for word in command if not self._is_redirect(word)]
                while words and self._restore(words[0]).strip("\"'") in ("command", "time", "!"):
                    words = words[1:]
                if not words:
                    continue
                head = self._restore(words[0])
                if caller == function and POSITIONAL_HEAD_RE.match(head):
                    continue  # o valor vem das próprias chamadas; conferido depois da união
                if "$" in head or "`" in head:
                    try:
                        heads = self.evaluate(words[0], number, caller, 1)
                    except _Undecided:
                        raise _Undecided("positional_parameter_call_unknown")
                    if any(value.split()[:1] == [function] for value in heads):
                        raise _Undecided("positional_parameter_call_unknown")
                elif head.strip("\"'") == function:
                    calls.append((number, caller, words[1:]))
        except _Undecided as exc:
            self._calls_cache[function] = exc
            raise
        self._calls_cache[function] = calls
        return calls

    def _unquoted_expansion(self, raw):
        bare = QUOTE_SLOT_RE.sub("", raw)
        return "$" in bare or "`" in bare

    def _is_redirect(self, raw):
        head = QUOTE_SLOT_RE.split(raw)[0]
        return bool(head) and REDIRECT_RE.match(head) is not None and not (head == raw and "$" in raw)

    def executed_paths(self):
        """Caminhos absolutos que o texto roda: comando literal com ``/`` e valor decidido na posição de comando."""
        found = set(self.decided_paths)
        for number, function, command, _separator, _origin, _line in self.words:
            tokens, index = [("raw", word, None) for word in command], 0
            while index < len(tokens):
                width = self._redirect(tokens, index)
                if not width:
                    break
                index += width  # ``< /proc/cmdline cmd``: o alvo do redirecionamento não roda
            head = self._restore(command[index]).strip("\"'") if index < len(command) else ""
            if head.startswith("/") and len(head) > 1 and "$" not in head and "`" not in head:
                found.add(_normal_path(head))
        return sorted(found)

    # ── Posição de comando, com a gramática dos wrappers. ──

    def command_items(self):
        """Cada expansão na posição de comando (ou depois de quem executa outro comando), já com a gramática dos
        wrappers: ``evaluate`` (palavra a avaliar), ``value`` (palavra que veio de expansão decidida), ``none`` (o
        wrapper não executa comando: IDs ou ajuda) e ``undecided``. Com a linha, a função e a origem da palavra."""
        items = []
        for number, function, command, _separator, origin, origin_line in self.words:
            where = {"line": number, "function": function, "origin": origin, "origin_line": origin_line}
            try:
                found = self._positions([("raw", word, None) for word in command], number, function, [])
            except _Undecided as exc:
                # A gramática do wrapper não decidiu: a palavra do recibo é a primeira com expansão no comando.
                word = next((self._restore(token) for token in command if "$" in self._restore(token)
                             or "`" in self._restore(token)), " ".join(self._restore(token) for token in command[:2]))
                found = [{"kind": "undecided", "error": exc, "text": word}]
            items.extend(dict(item, **where) for item in found)
        return items

    def _redirect(self, tokens, index):
        """Palavras que o redirecionamento em ``index`` ocupa (0 quando não é redirecionamento)."""
        kind, text = tokens[index][0], tokens[index][1]
        if kind == "raw":
            if not self._is_redirect(text):
                return 0
            head = QUOTE_SLOT_RE.split(text)[0]
        else:
            if not REDIRECT_RE.match(text or "\x00"):
                return 0
            head = text
        target = REDIRECT_RE.match(head).group(1)
        return 1 if target or head != text else 2

    def _text(self, token, number, function):
        """Texto exato de uma palavra sem expansão (aspas tiradas); ``None`` quando há expansão."""
        kind, text = token[0], token[1]
        if kind == "text":
            return text
        restored = self._restore(text)
        if "$" in restored or "`" in restored:
            return None
        values = self.evaluate(text, number, function, 1)
        return next(iter(values)) if len(values) == 1 else None

    def _fields(self, token, number, function):
        """Palavras em que um token com expansão vira: sem aspas divide nos espaços (vazio some); com aspas é uma."""
        values = self.evaluate(token[1], number, function, 0)
        unquoted = self._unquoted_expansion(token[1])
        whole = unquoted and QUOTE_SLOT_RE.search(token[1]) is None
        out = []
        for value in sorted(values):
            if unquoted and GLOB_RE.search(value):
                raise _Undecided("wrapper_field_glob")
            if whole:
                out.append([("text", word, token[1]) for word in value.split()])
            elif unquoted and value.split() != [value]:
                raise _Undecided("wrapper_field_split")
            else:
                out.append([("text", value, token[1])])
        return out

    def _positions(self, tokens, number, function, chain, raws_before=()):
        index = 0
        while index < len(tokens):
            width = self._redirect(tokens, index)
            if not width:
                break
            index += width
        expanded = sorted({item[2] for item in tokens if item[0] == "text" and item[2]} | set(raws_before))
        if index >= len(tokens):
            return [{"kind": "none", "mode": "no_command", "wrapper": chain, "raws": expanded}] if chain else []
        token, rest = tokens[index], tokens[index + 1:]
        if token[0] == "raw" and ("$" in self._restore(token[1]) or "`" in self._restore(token[1])):
            return [{"kind": "evaluate", "raw": token[1], "place": "command", "wrapper": chain, "raws": expanded}]
        text = self._text(token, number, function)
        if text is None:
            return []
        name = text.rsplit("/", 1)[-1]
        found = []
        if token[0] == "text":
            found.append({"kind": "value", "text": text, "place": "command", "wrapper": chain, "raws": expanded})
        if _interpreter_named(name) is not None:
            # O script (ou o texto de ``-c``) que o interpretador lê é varrido pelo mesmo contrato.
            found.append({"kind": "interpreter", "text": text, "tokens": rest, "wrapper": chain})
            return found
        if EXECUTED_PATH_RE.fullmatch(text) and name not in WRAPPER_GRAMMARS and name not in CITE_RUNNERS:
            found.append({"kind": "executed", "text": text, "wrapper": chain})
        if name in WRAPPER_GRAMMARS:
            for outcome, seq, position, branch in self._wrap(name, rest, number, function):
                raws = sorted(set(branch) | set(expanded))
                if outcome == "command":
                    found.extend(self._positions(seq[position:], number, function, chain + [name], raws))
                elif outcome == "program":
                    found.extend(self._program(seq[position], chain + [name], raws))
                else:
                    found.append({"kind": "none", "mode": outcome, "wrapper": chain + [name], "raws": raws})
            return found
        if name == "eval":
            if self.has_eval:
                found.append({"kind": "undecided", "text": " ".join(
                    ["eval"] + [self._restore(item[1]) for item in rest if item[0] == "raw"])[:120],
                    "error": _Undecided("eval", None, self.eval_origin, self.eval_line)})
            return found
        if name in CITE_RUNNERS:
            for item in rest:
                if item[0] == "text":
                    found.append({"kind": "value", "text": item[1], "place": "runner_argument", "wrapper": chain,
                                  "raws": [item[2]]})
                elif "$" in self._restore(item[1]) or "`" in self._restore(item[1]):
                    found.append({"kind": "evaluate", "raw": item[1], "place": "runner_argument",
                                  "wrapper": chain, "raws": expanded})
        return found

    def _program(self, token, chain, raws):
        """Valor de ``su -s``/``runuser --shell``: é o programa que roda (posição de comando)."""
        if token[0] == "text":
            return [{"kind": "value", "text": token[1], "place": "command", "wrapper": chain, "raws": raws}]
        if "$" in self._restore(token[1]) or "`" in self._restore(token[1]):
            return [{"kind": "evaluate", "raw": token[1], "place": "command", "wrapper": chain, "raws": raws}]
        return []

    def _wrap(self, name, tokens, number, function):
        """Ramos da gramática do wrapper: ``(saída, palavras, posição)``; expansão sem aspas antes do comando vira as
        palavras de cada valor decidido (um ramo por valor), e opção fora da lista é indecidível."""
        done, stack = [], [(list(tokens), frozenset())]
        while stack:
            seq, raws = stack.pop()
            outcome = self._grammar(name, seq, number, function)
            if outcome[0] == "expand":
                position = outcome[1]
                expanded = raws | {seq[position][1]}
                for fields in self._fields(seq[position], number, function):
                    stack.append((seq[:position] + fields + seq[position + 1:], expanded))
                if len(stack) + len(done) > MAX_SHELL_VALUES:
                    raise _Undecided("too_many_values", wrapper=name)
            else:
                done.append((outcome[0], seq, outcome[1], raws))
        return done

    def _look(self, seq, position, need_text, number, function):
        """``("expand", i)`` quando a palavra precisa do valor (ou divide em palavras), ``("opaque",)`` para uma
        palavra entre aspas cujo valor não importa, ``("text", texto)`` para literal."""
        token = seq[position]
        if token[0] == "raw":
            restored = self._restore(token[1])
            if "$" in restored or "`" in restored:
                if need_text or self._unquoted_expansion(token[1]):
                    return ("expand", position)
                return ("opaque",)
        return ("text", self._text(token, number, function))

    def _option_value(self, seq, position, kind, name, number, function):
        """Valor de uma opção: ``shell`` com expansão é indecidível; ``program`` volta a posição do programa."""
        if position >= len(seq):
            raise _Undecided("wrapper_option_missing_value", wrapper=name)
        seen = self._look(seq, position, False, number, function)
        if seen[0] == "expand":
            return seen
        if kind == "shell" and seen[0] == "opaque":
            raise _Undecided("wrapper_shell_command", wrapper=name)
        if kind == "program":
            return ("program", position)
        return None

    def _grammar(self, name, seq, number, function):
        """Uma passada da gramática sobre as palavras: onde está o comando, ou por que não há comando."""
        spec = WRAPPER_GRAMMARS[name]
        flags, args, end = spec.get("flags", ""), spec.get("args", ""), spec.get("end", "")
        shell, program, user = spec.get("shell", ""), spec.get("program", ""), spec.get("user", "")
        operands_needed, operands, user_mode, shell_command = spec.get("operands", 0), [], False, False
        index, options_open = 0, True
        while index < len(seq):
            width = self._redirect(seq, index)
            if width:
                index += width
                continue
            seen = self._look(seq, index, options_open, number, function)
            if seen[0] == "expand":
                return seen
            text = seen[1] if seen[0] == "text" else None
            if options_open and text == "--":
                options_open = False
                index += 1
                continue
            if options_open and text and text.startswith("--"):
                option, equal, value = text[2:].partition("=")
                if option in spec.get("long_end", ()):
                    return ("ids" if option in ("pid", "pgid", "uid") else "info", None)
                if option in spec.get("long_refuse", ()):
                    raise _Undecided("wrapper_split_string", wrapper=name)
                if (option in spec.get("long_flags", ()) and not equal) or option in spec.get("long_optional", ()):
                    index += 1
                    continue
                kind = ("arg" if option in spec.get("long_args", ()) else
                        "shell" if option in spec.get("long_shell", ()) else
                        "program" if option in spec.get("long_program", ()) else
                        "user" if option in spec.get("long_user", ()) else None)
                if kind is None:
                    raise _Undecided("wrapper_option_unknown", wrapper=name)
                user_mode = user_mode or kind == "user"
                shell_command = shell_command or kind == "shell"
                if equal:
                    if kind == "shell" and ("$" in value or "`" in value):
                        raise _Undecided("wrapper_shell_command", wrapper=name)
                    if kind == "program":
                        raise _Undecided("wrapper_program_attached", wrapper=name)
                else:
                    index += 1
                    taken = self._option_value(seq, index, kind, name, number, function)
                    if taken:
                        return taken
                index += 1
                continue
            if options_open and text and text.startswith("-") and len(text) > 1:
                cluster = text[1:]
                for position, char in enumerate(cluster):
                    if char in end:
                        return ("ids" if char in "pPu" else "info", None)
                    if char in spec.get("refuse", ""):
                        raise _Undecided("wrapper_split_string", wrapper=name)
                    if char in flags:
                        continue
                    kind = ("arg" if char in args else "shell" if char in shell else
                            "program" if char in program else "user" if char in user else None)
                    if kind is None:
                        raise _Undecided("wrapper_option_unknown", wrapper=name)
                    user_mode = user_mode or kind == "user"
                    shell_command = shell_command or kind == "shell"
                    attached = cluster[position + 1:]
                    if attached:
                        if kind == "shell" and ("$" in attached or "`" in attached):
                            raise _Undecided("wrapper_shell_command", wrapper=name)
                        if kind == "program":
                            raise _Undecided("wrapper_program_attached", wrapper=name)
                    else:
                        index += 1
                        taken = self._option_value(seq, index, kind, name, number, function)
                        if taken:
                            return taken
                    break
                index += 1
                continue
            if options_open and text == "-" and spec.get("dash"):
                index += 1
                continue
            if options_open and spec.get("assignments") and text and SHELL_ASSIGNMENT_RE.match(text):
                index += 1
                continue
            if spec.get("permute"):
                operands.append(index)
                index += 1
                continue
            if len(operands) < operands_needed:
                operands.append(index)
                index += 1
                marker = spec.get("after_operand_shell")
                if marker and index < len(seq):
                    after = self._look(seq, index, True, number, function)
                    if after[0] == "expand":
                        return after
                    if after[0] == "text" and after[1] in marker:
                        if index + 1 < len(seq) and self._look(seq, index + 1, False, number, function)[0] in (
                                "opaque", "expand"):
                            raise _Undecided("wrapper_shell_command", wrapper=name)
                        return ("shell_command", None)
                continue
            return ("command", index)
        if spec.get("permute"):
            if user_mode:
                return ("command", operands[0]) if operands else ("no_command", None)
            if not shell_command:
                for position in operands[1:]:
                    if seq[position][0] == "text" or "$" in self._restore(seq[position][1]):
                        raise _Undecided("wrapper_shell_argument", wrapper=name)
            return ("shell_command" if shell_command else "login_shell", None)
        return ("shell_command" if shell_command else "no_command", None)


def _mask_quotes_keep(text):
    """Texto de shell lido caractere a caractere: cada trecho entre aspas vira uma palavra (``$\\x01n\\x01`` quando
    tem ``$`` entre aspas duplas, ``\\x01n\\x01`` nas outras), comentário e corpo de heredoc saem, escape fica como
    está. Devolve ``(texto, balanced, trechos, início)``; aspa sem par deixa ``balanced`` falso e ``início`` diz onde
    ela (ou o heredoc sem terminador) começa. O texto devolvido tem as linhas
    do original: a quebra que some (continuação com ``\\``, aspas em várias linhas) volta na próxima quebra real e o
    corpo do heredoc vira linha vazia."""
    text = ARITHMETIC_RE.sub("0", text)
    out, quoted, heredocs = [], [], []
    position, size, balanced, pending, failed_at = 0, len(text), True, 0, None

    def slot(chunk):
        quoted.append(chunk)
        marker = "$" if chunk.startswith('"') and "$" in chunk else ""
        out.append("%s\x01%d\x01" % (marker, len(quoted) - 1))

    while position < size:
        char = text[position]
        if char == "\n":
            out.append("\n" * (1 + pending))
            position, pending = position + 1, 0
            while heredocs and position < size:
                end = text.find("\n", position)
                end = size if end < 0 else end
                line = text[position:end]
                position = end + 1
                out.append("\n")
                strip, word = heredocs[0]
                if (line.lstrip("\t") if strip else line) == word:
                    heredocs.pop(0)
            continue
        if char == "\\":
            if text.startswith("\\\n", position):
                out.append(" ")
                pending += 1
            else:
                out.append(text[position:position + 2])
            position += 2
        elif char == "#" and (position == 0 or text[position - 1] in " \t\n;&|("):
            end = text.find("\n", position)
            position = size if end < 0 else end
        elif char == "'" or (char == "$" and text.startswith("$'", position)):
            begin = position + (2 if char == "$" else 1)
            end = begin
            while end < size and text[end] != "'":
                end += 2 if char == "$" and text[end] == "\\" else 1
            if end >= size:
                balanced, failed_at = False, position
                break
            slot("'" + text[begin:end] + "'")
            pending += text.count("\n", position, end)
            position = end + 1
        elif char == '"':
            end = _double_quote_end(text, position + 1)
            if end >= size:
                balanced, failed_at = False, position
                break
            slot(text[position:end + 1])
            pending += text.count("\n", position, end)
            position = end + 1
        elif text.startswith("<<", position) and not text.startswith("<<<", position):
            match = HEREDOC_RE.match(text, position)
            if match:
                heredocs.append((bool(match.group(1)), match.group(2)))
                failed_at = position if len(heredocs) == 1 else failed_at
                out.append("<<H")
                position = match.end()
            else:
                out.append(char)
                position += 1
        else:
            out.append(char)
            position += 1
    if heredocs:
        balanced = False
    elif balanced:
        failed_at = None
    out.append("\n" * pending)
    return "".join(out), balanced, quoted, failed_at


def _double_quote_end(text, position):
    """Posição da aspa que fecha a aspa dupla aberta antes de ``position``; ``$(...)`` e crase dentro dela têm aspas
    próprias (``"$(grep "a b" f)"``). ``len(text)`` quando não fecha."""
    size = len(text)
    while position < size:
        char = text[position]
        if char == "\\":
            position += 2
        elif char == '"':
            return position
        elif text.startswith("$(", position):
            position = _paren_end(text, position + 2) + 1
        elif char == "`":
            position += 1
            while position < size and text[position] != "`":
                position += 2 if text[position] == "\\" else 1
            position += 1
        else:
            position += 1
    return size


def _paren_end(text, position):
    """Posição do ``)`` que fecha ``$(`` aberto antes de ``position``, pulando aspas; ``len(text)`` quando não fecha."""
    size, depth = len(text), 1
    while position < size:
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == "'":
            position = text.find("'", position + 1)
            if position < 0:
                return size
        elif char == '"':
            position = _double_quote_end(text, position + 1)
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return size

def _cron_kind(source):
    """Gramática do crontab pela fonte: ``system`` (``/etc/crontab``, ``cron.d``), ``user`` (spool), ``ana`` ou nenhuma."""
    if source == "/etc/anacrontab":
        return "ana"
    if source == "/etc/crontab" or source.startswith("/etc/cron.d/"):
        return "system"
    if source.startswith(CRON_TABLE_DIRS[1] + "/"):
        return "user"
    return None


def _mask_quotes(text):
    """Texto com cada trecho entre aspas trocado por uma palavra: separador dentro de aspas não separa comando.
    Aspas duplas com ``$`` viram ``Q$`` (``"$PY"`` no lugar do comando continua expansão); simples viram ``Q``.
    Aspa que sobra sem par devolve ``balanced`` falso (o texto não se lê)."""
    parts, position, balanced = [], 0, True
    text = ARITHMETIC_RE.sub("A", text)  # $(( ... )) é aritmética: não roda comando
    for match in QUOTED_RE.finditer(text):
        parts.append(text[position:match.start()])
        quoted = match.group(0)
        parts.append("Q$" if quoted.startswith('"') and "$" in quoted else "Q")
        position = match.end()
    rest = text[position:]
    if re.search(r"(?<!\\)['\"]", rest):
        balanced = False
    parts.append(rest)
    return "".join(parts), balanced


def _command_words(text):
    """Palavras depois das palavras de controle e das atribuições: a primeira é o comando."""
    words = text.split()
    while words and (words[0] in SCRIPTLET_KEYWORDS or words[0] in SHELL_DECLARATIONS
                     or SHELL_ASSIGNMENT_RE.match(words[0])):
        words = words[1:]
    return words


def _command_position_expansion(line):
    """Expansão na posição de comando de uma linha de shell: a primeira palavra de um comando (separado por ``;``,
    ``&``, ``|``, parênteses ou substituição) com ``$`` (``"$PY" x.py``, ``${PYTHON:-python3.6} x.py``), qualquer
    palavra com ``$`` depois de quem executa outro comando (``sudo``, ``exec``, ``eval``, ``.``) ou substituição de
    comando no lugar do comando (``$(f) x``). Aspas não separam comando; ``case`` e o teste ``[[ ... ]]`` não têm comando."""
    line = DOUBLE_BRACKET_RE.sub(" [[ ]] ", line)
    for match in COMMAND_SUBSTITUTION_RE.finditer(line):
        before = CITE_SEPARATOR_RE.split(line[:match.start()])[-1]
        # ``X=$(cmd)`` é atribuição: a saída não roda como comando.
        if not _command_words(before) and not before.rstrip().endswith("="):
            return True
    for segment in CITE_SEPARATOR_RE.split(line):
        if segment.split()[:1] == ["case"]:
            continue
        words = _command_words(segment)
        if not words:
            continue
        if "$" in words[0] and not words[0].startswith("$(("):
            return True
        if words[0].strip("\"'").rsplit("/", 1)[-1] in CITE_RUNNERS and any("$" in word for word in words[1:]):
            return True
    return False


def _bus_label(name):
    """Nome de unidade no caminho de objeto do systemd (``bus_label_escape``): fora de ``[A-Za-z0-9]`` vira ``_xx``."""
    return "".join(char if (char in BUS_LABEL_CHARS and (index or char not in "0123456789"))
                   else "_%02x" % ord(char) for index, char in enumerate(name))


def _command_name(word):
    """Nome do comando que a palavra executa: o nome final, exceto caminho absoluto fora do PATH (``/etc/crontab``),
    que é dado e não comando. Diretório relativo ou por variável (``$BIN/crontab``) conta, pelo lado seguro."""
    if "/" in word:
        word = _normal_path(word)
    directory, _, name = word.rpartition("/")
    if directory.startswith("/") and directory not in STANDARD_BIN_DIRS:
        return word
    return name if "/" in word else word


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
        data, cut = self.read_bytes(path, limit)
        return data.decode("utf-8", "replace"), cut

    def read_bytes(self, path, limit):
        """(bytes, cortado), com as mesmas garantias de ``read``: o ELF se lê sem decodificar."""
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
        return data[:limit], len(data) > limit

    def identity(self):
        return {"pid": os.getpid(), "session": os.getsid(0), "euid": os.geteuid()}


def _usrmerge(path):
    for legacy, merged in USRMERGE:
        if path.startswith(legacy):
            return merged + path[len(legacy):]
    return path


def _elf_identity(data):
    """``(classe, máquina)`` do cabeçalho ELF; ``None`` quando não é ELF ou o cabeçalho não se lê."""
    if data[:4] != b"\x7fELF" or data[4:5] not in (b"\x01", b"\x02") or data[5:6] not in (b"\x01", b"\x02"):
        return None
    try:
        machine = struct.unpack_from("<H" if data[5:6] == b"\x01" else ">H", data, 18)[0]
    except struct.error:
        return None
    return (2 if data[4:5] == b"\x02" else 1), machine


def _elf_dynamic(data):
    """``{"class", "machine", "needed", "search"}`` do ELF (``search``: RUNPATH, ou RPATH sem RUNPATH, como o
    ``ld.so``); ``None`` quando não é ELF; ``ValueError`` quando é ELF e a seção dinâmica não se lê inteira."""
    if data[:4] != b"\x7fELF":
        return None
    elf_class, encoding = data[4:5], data[5:6]
    if elf_class not in (b"\x01", b"\x02") or encoding not in (b"\x01", b"\x02"):
        raise ValueError("elf_header_unknown")
    wide, end = elf_class == b"\x02", "<" if encoding == b"\x01" else ">"
    try:
        machine = struct.unpack_from(end + "H", data, 18)[0]
        phoff = struct.unpack_from(end + ("Q" if wide else "I"), data, 32 if wide else 28)[0]
        phentsize, phnum = struct.unpack_from(end + "HH", data, 54 if wide else 42)
        loads, dynamic = [], None
        for index in range(phnum):
            offset = phoff + index * phentsize
            if wide:
                kind, _, p_offset, vaddr, _, filesz, _ = struct.unpack_from(end + "IIQQQQQ", data, offset)
            else:
                kind, p_offset, vaddr, _, filesz, _ = struct.unpack_from(end + "IIIIII", data, offset)
            if kind == 1:
                loads.append((vaddr, p_offset, filesz))
            elif kind == 2:
                dynamic = (p_offset, filesz)
        result = {"class": 2 if wide else 1, "machine": machine, "needed": [], "search": []}
        if dynamic is None:
            return result  # estático: não abre biblioteca
        size, layout = (16, end + "qQ") if wide else (8, end + "iI")
        needed, strtab, rpath, runpath = [], None, [], []
        for offset in range(dynamic[0], dynamic[0] + dynamic[1] - size + 1, size):
            tag, value = struct.unpack_from(layout, data, offset)
            if tag == 0:
                break
            if tag == 1:
                needed.append(value)
            elif tag == 5:
                strtab = value
            elif tag == 15:
                rpath.append(value)
            elif tag == 29:
                runpath.append(value)
        if strtab is None:
            raise ValueError("elf_strtab_absent")
        base = next((p_offset + strtab - vaddr for vaddr, p_offset, filesz in loads
                     if vaddr <= strtab < vaddr + filesz), None)
        if base is None:
            raise ValueError("elf_strtab_unmapped")

        def text(index):
            start = base + index
            stop = data.find(b"\x00", start)
            if start >= len(data) or stop < 0:
                raise ValueError("elf_string_cut")
            return data[start:stop].decode("utf-8")

        result["needed"] = [text(index) for index in needed]
        result["search"] = [part for index in (runpath or rpath) for part in text(index).split(":") if part]
        return result
    except (struct.error, UnicodeDecodeError) as exc:
        raise ValueError("elf_dynamic_unread") from exc


class _Paths:
    """Forma comparável de um caminho: usrmerge e ``realpath`` dos dois lados (``rpm -ql`` e o observado)."""

    def __init__(self, fs, owners=None):
        self.fs, self.cache, self.owners = fs, {}, owners
        # Forma que é o próprio arquivo do pacote, e forma que só existe pelo link dele: (pacote -> links).
        self.direct, self.links = {}, {}

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
                if self._device(path):
                    continue  # /dev/null e afins são abertos por qualquer processo: não provam uso do pacote
                forms = self.forms(path)
                for form in forms:
                    index.setdefault(form, set()).add(package)
                self.direct.setdefault(forms[0], set()).add(package)
                if len(forms) > 1:
                    self.links.setdefault(forms[-1], {}).setdefault(package, set()).add(forms[0])
        return index

    def ownership(self, package, forms, path):
        """Dono RPM do caminho observado e do arquivo resolvido, e se o match só existe pelo link do pacote."""
        resolved = forms[-1]
        path_owner = self.owners.package(path)
        resolved_owner = path_owner if resolved == path else self.owners.package(resolved)
        direct = any(package in self.direct.get(form, ()) for form in forms)
        roots = sorted({root for form in forms for root in self.links.get(form, {}).get(package, ())})
        # Lado seguro: só links da raiz (até o teto, todos listados para B buscar) e dono resolvido único.
        via = (not direct and 0 < len(roots) <= MAX_LINK_PATHS and resolved_owner is not None
               and resolved_owner != package)
        return {"path_owner": path_owner, "resolved_path": resolved, "resolved_owner": resolved_owner,
                "via_link_of_root": via, "root_link_paths": roots[:MAX_LINK_PATHS] if via else []}

    def _device(self, path):
        try:
            mode = self.fs.mode(path)
        except OSError:
            return False
        return stat.S_ISCHR(mode) or stat.S_ISBLK(mode)


class _Matches:
    """Arquivos de pacote pedido achados em uso: contagem por pacote e até MAX_MATCHES exemplos.

    ``add`` compara pelo usrmerge e pelo ``realpath``; ``add_text`` (texto de crontab e script) só pelo
    usrmerge, porque o programa que a entrada roda já entra pelo ``add`` quando é resolvido.
    """

    def __init__(self, index, paths=None):
        self.index, self.paths = index, paths
        self.found = collections.defaultdict(list)
        self.count = collections.Counter()
        self.sources = collections.defaultdict(set)  # pacotes citados por fonte, sem o teto de MAX_MATCHES

    def add(self, path, **where):
        self._record(self.paths.forms(path) if self.paths else (_usrmerge(path),), path, where)

    def add_text(self, path, **where):
        self._record((_usrmerge(path),), path, where)

    def _record(self, forms, path, where):
        packages = set()
        for form in forms:
            packages |= self.index.get(form, set())
        if "source" in where:
            self.sources[where["source"]].update(packages)
        for package in sorted(packages):
            self.count[package] += 1
            if len(self.found[package]) < MAX_MATCHES:
                match = dict(where, path=path)
                if self.paths is not None and self.paths.owners is not None:
                    match.update(self.paths.ownership(package, forms, path))
                self.found[package].append(match)


class _Capped:
    """Lista com teto. O que passa do teto só soma em ``over``: o recibo diz quantas entradas ficaram de fora."""

    def __init__(self, limit):
        self.limit, self.items, self.over = limit, [], 0

    def add(self, item):
        if len(self.items) < self.limit:
            self.items.append(item)
        else:
            self.over += 1


class _Owners:
    """Dono RPM de arquivo citado: ``owned``, ``unowned``, ``failed`` ou ``over_limit``; uma consulta por caminho."""

    def __init__(self, run):
        self.run, self.cache, self.names, self.left = run, {}, {}, MAX_OWNER_QUERIES

    def __call__(self, path):
        if path not in self.cache:
            if self.left <= 0:
                return "over_limit"
            self.left -= 1
            names, reason = _owner_lookup(path, self.run)
            self.names[path] = names
            self.cache[path] = "owned" if names else "unowned" if reason == "unowned_binary" else "failed"
        return self.cache[path]

    def prefetch(self, paths):
        """Dono de vários arquivos numa consulta do ``rpm -qf`` (uma linha por arquivo, na ordem). Saída que não
        alinha (arquivo com mais de um dono, ausente) não grava nada: o caminho segue para a consulta por arquivo."""
        pending = [path for path in dict.fromkeys(paths) if path not in self.cache]
        for start in range(0, len(pending), 64):
            batch = pending[start:start + 64]
            shown = self.run(["rpm", "-qf", "--qf", "%{NAME}\n", "--", *batch])
            lines = [line.strip() for line in shown["stdout"].splitlines() if line.strip()]
            if shown["truncated"] or shown["rc"] not in (0, 1) or len(lines) != len(batch):
                continue
            for path, line in zip(batch, lines):
                if UNOWNED_RE.fullmatch(line):
                    self.names[path], self.cache[path] = None, "unowned"
                elif _package_names(line + "\n"):
                    self.names[path], self.cache[path] = _package_names(line + "\n"), "owned"

    def package(self, path):
        """Pacote dono único do arquivo, ou ``None``: sem dono, com mais de um dono ou sem consulta disponível."""
        self(path)
        names = self.names.get(path)
        return names[0] if names and len(names) == 1 else None


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
    kind, program, _ = _argv_scan(spec, argv)
    return kind, program


def _argv_scan(spec, argv):
    """``_argv_program`` mais a posição (no argv) do token que decidiu o resultado. Só a posição sai daqui, nunca o valor."""
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg == "-":
            return "inline", None, i
        if not (arg.startswith("-") or (spec is SHELL and arg.startswith("+"))):
            break
        if arg.startswith("--"):
            key = arg.split("=", 1)[0]
            if key in spec.long_with_arg:
                i += 1 if "=" in arg else 2
            elif key in spec.long_no_arg:
                i += 1
            else:
                return "ambiguous", None, i
            continue
        if len(arg) == 1:
            return "ambiguous", None, i
        step = 1
        for position, letter in enumerate(arg[1:], 1):
            if letter in spec.inline:
                return "inline", None, i
            if letter in spec.with_arg:
                step = 2 if position == len(arg) - 1 else 1
                break
            if letter not in spec.no_arg:
                return "ambiguous", None, i
        i += step
    if i < len(argv):
        return "program", argv[i], i
    return "none", None, i


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


def _process_unit(pid, fs):
    """Unidade systemd do pid lida de ``/proc/<pid>/cgroup`` (v1 ``name=systemd``, depois v2); ``None`` sem serviço."""
    try:
        text, _ = fs.read(f"/proc/{pid}/cgroup", 4096)
    except OSError:
        return None
    lines = [line.split(":", 2) for line in text.splitlines()]
    for hierarchy in ("name=systemd", ""):
        for parts in lines:
            if len(parts) == 3 and parts[1] == hierarchy:
                for name in reversed(parts[2].strip().split("/")):
                    if SERVICE_RE.match(name):
                        return name
    return None


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
    blocking = _Capped(MAX_BLOCKING)
    # Orçamento próprio: o dono do executável não consome o dos interpretadores, então o veredito não muda.
    exe_owners = _Owners(owners.run)
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
        inline = _inline_indexes(exe, argv)
        if inline:
            reasons.add("interpreter_inline_code")
            blocking.add(_blocking_entry(pid, exe, "interpreter_inline_code", inline))
        grammar = EXEC_GRAMMARS.get(exe.rsplit("/", 1)[-1])
        exempt, forced = (_grammar_scan(grammar, argv)
                          if grammar is not None and exe_owners.package(exe) == grammar.package
                          else (frozenset(), frozenset()))
        arguments = _process_argument_blocks(judge, argv, pid, fs, script, exempt, forced)
        if arguments:
            reasons.add("process_argument_unresolved")
            blocking.add(_blocking_entry(pid, exe, "process_argument_unresolved", arguments))
        hits = [(via, path) for via, path in sorted(found) if _package_hit(path, paths, index)]
        if hits:
            # Dono do executável só para quem usa arquivo de pacote pedido; o cache de _Owners evita repetir por exe.
            exe_owner, unit = exe_owners.package(exe), _process_unit(pid, fs)
            for via, path in hits:
                matches.add(path, pid=int(pid), via=via, exe=exe, exe_owner=exe_owner, unit=unit)
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
              "interpreters_unattributed": dict(sorted(unattributed.items())),
              "blocking": blocking.items, "blocking_over_limit": blocking.over}
    return source, states


def _script_text(text):
    """Script agendado sem comentário; atribuição fica, porque pode citar arquivo do pacote. A linha de comentário fica
    vazia: o número de linha que o recibo grava é o do arquivo."""
    return "\n".join("" if line.strip().startswith("#") else line for line in text.splitlines())


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


def _option_values(grammar, argv):
    """Opções com valor lidas como o getopt. Cada item: (índice da opção, índice do valor ou ``None``, é opção de
    programa, é opção de dado, é opção excluída).

    Valor separado consome o próximo argumento; valor colado (``-Ovalor``, ``--opt=valor``) fica no próprio token.
    Posicional não encerra as opções (o getopt permuta). ``--`` encerra. Opção que a gramática não conhece não consome.
    """
    found, index = [], 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            break
        if arg.startswith("--"):
            name, equals, _ = arg[2:].partition("=")
            if name not in grammar.value_long and name:
                # argparse aceita prefixo de opção longa (``--conf`` = ``--config``): prefixo de opção excluída exclui.
                prefixed = [long for long in grammar.excluded_long if long.startswith(name)]
                name = prefixed[0] if len(prefixed) == 1 else name
            if name in grammar.value_long:
                flags = (name in grammar.program_long, name in grammar.payload_long, name in grammar.excluded_long)
                if equals:
                    found.append((index, index, *flags))
                elif index + 1 < len(argv):
                    found.append((index, index + 1, *flags))
                    index += 1
                else:
                    found.append((index, None, *flags))
        elif arg.startswith("-") and len(arg) > 1:
            position = 1
            while position < len(arg):
                letter = arg[position]
                if letter in grammar.value_short:
                    flags = (letter in grammar.program_short, letter in grammar.payload_short,
                             letter in grammar.excluded_short)
                    if position + 1 < len(arg):
                        found.append((index, index, *flags))
                    elif index + 1 < len(argv):
                        found.append((index, index + 1, *flags))
                        index += 1
                    else:
                        found.append((index, None, *flags))
                    break
                if letter in grammar.optional_short:
                    break
                position += 1
        index += 1
    return found


def _grammar_scan(grammar, argv):
    """(dado, bloqueio): índices de valor que a gramática lê como dado, e índices de opção excluída (carrega módulo ou
    configuração interpretada), que bloqueiam sempre. Com opção de programa (``-l``) presente, nenhum dado sai da lista:
    o caminho dela é o programa e é julgado como qualquer argumento."""
    found = _option_values(grammar, argv)
    blocking = frozenset(option for option, _, _, _, excluded in found if excluded)
    if any(is_program for _, _, is_program, _, _ in found):
        return frozenset(), blocking
    data = frozenset(value for _, value, _, is_payload, _ in found if is_payload and value is not None)
    return data, blocking


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


def _package_hit(path, paths, index):
    """Caminho observado que é arquivo de algum pacote pedido, pelas mesmas formas que o índice usa."""
    return any(form in index for form in paths.forms(path))


def _process_argument_blocks(judge, argv, pid, fs, script, exempt=frozenset(), forced=frozenset()):
    """Posições no argv dos argumentos que bloqueiam ``not_used``. argv[0] (título do processo), o script que o
    interpretador lê e os valores que a gramática lê como dado (``exempt``) ficam fora. Opção excluída pela gramática
    (``forced``) bloqueia sempre. O PATH do processo só é lido quando há argumento."""
    indexes = [index for index in range(1, len(argv)) if index != script and index not in exempt]
    if not indexes:
        return []
    dirs = _process_path_dirs(pid, fs)
    return [index for index in indexes if index in forced or _process_token_blocks(judge, argv[index], dirs)]


def _inline_indexes(exe, argv):
    """Posições no argv do código inline que o interpretador roda (``-c``, ``-e``, ``-`` e equivalentes pela tabela).
    Para interpretador desconhecido, vale a opção de ``INLINE_CODE_OPTIONS`` seguida de argumento."""
    spec = _interpreter_named(exe.rsplit("/", 1)[-1])
    found = set()
    if spec is not None and argv:
        kind, _, index = _argv_scan(spec, argv)
        if kind == "inline":
            found.add(index)
    found.update(index for index, arg in enumerate(argv)
                 if index and arg in INLINE_CODE_OPTIONS and index + 1 < len(argv))
    return sorted(found)


def _blocking_entry(pid, exe, reason, indexes):
    """Processo que bloqueia ``not_used``: pid, executável e posições no argv. O valor de argumento nunca entra."""
    return {"pid": int(pid), "exe": exe, "reason": reason, "argument_indexes": indexes}


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
        self.unresolved = _Capped(MAX_UNRESOLVED_ENTRIES)
        self.unresolved_by_source = collections.Counter()  # sem teto: o spool sabe quantas entradas não resolveu
        self.judge = _TokenJudge(fs, paths, self.owned)
        self.script_dirs = {paths.final(directory) for directory in CRON_SCRIPT_DIRS}
        self.token_failure = None  # índice do primeiro token sem resolução do Exec* em análise (só o índice sai)
        self.grammar_state = None  # motivo da gramática não aplicada ao Exec* em análise (código fixo, sem texto)
        self.alias_cache = {}  # diretório de aliases do dnf: texto lido uma vez por execução (None = ilegível)
        self.spools = {}  # crontab de usuário e at: lido por inteiro, arquivos lidos e pacotes citados (``spools``)

    def expired(self):
        if time.monotonic() > self.deadline:
            self.reasons.add("time_budget")
            return True
        return False

    def text(self, path, spool=None):
        try:
            text, cut = self.fs.read(path, FILE_LIMIT)
        except (FileNotFoundError, NotRegularFile):
            return None
        except OSError:
            self.reasons.add("schedule_source_unreadable")
            self.spool_gap(spool)
            return None
        self.counts["files"] += 1
        if cut:
            self.reasons.add("schedule_file_over_limit")
            self.spool_gap(spool)
        if spool is not None:
            self.spools[spool]["files"].append(path)
        return text

    def spool_gap(self, spool):
        if spool is not None:
            self.spools[spool]["read"] = False

    def listing(self, directory, spool=None):
        """Arquivos do diretório. ``spool`` (crontab de usuário ou ``at``) registra se a leitura foi inteira."""
        if spool is not None:
            self.spools[spool] = {"directory": directory, "read": True, "files": []}
        try:
            names = self.fs.listdir(directory)
        except FileNotFoundError:
            return []
        except OSError:
            self.reasons.add("schedule_source_unreadable")
            self.spool_gap(spool)
            return []
        names = [name for name in names if not name.startswith(".") and not name.endswith(SKIPPED_SUFFIXES)]
        if len(names) > MAX_SCHEDULE_FILES:
            self.reasons.add("schedule_files_over_limit")
            self.spool_gap(spool)
            names = names[:MAX_SCHEDULE_FILES]
        return ["%s/%s" % (directory, name) for name in names]

    def spool_summary(self):
        """Por spool: diretório, leitura inteira, arquivos lidos, pacotes pedidos que alguma entrada cita e quantas
        entradas ficaram sem resolução (spool com entrada sem resolução não fecha a origem)."""
        return {key: {"directory": state["directory"], "read": state["read"], "files": len(state["files"]),
                      "cited_packages": sorted({name for path in state["files"]
                                                for name in self.matches.sources.get(path, ())}),
                      "unresolved_entries": sum(self.unresolved_by_source[path] for path in set(state["files"]))}
                for key, state in sorted(self.spools.items())}

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

    def outcome(self, status, source, line=None, detail=None):
        """Conta a entrada sem resolução: ``unresolved``, ou ``not_allowlisted`` quando a forma está fora da lista.
        A sem resolução vai para ``unresolved_entries`` com a fonte e a linha (ou o timer), nunca com o comando. ``detail``
        é um código fixo, sem texto do comando nem valor de variável."""
        if status != RESOLVED:
            self.counts["unresolved" if status == UNRESOLVED else "not_allowlisted"] += 1
        if status == UNRESOLVED:
            self.unresolved_by_source[source] += 1
            entry = {"source": source, "reason": "schedule_command_unresolved"}
            if line is not None:
                entry["line"] = line
            if detail is not None:
                entry["detail"] = detail
            self.unresolved.add(entry)

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
            try:
                body, cut = self.fs.read(script, FILE_LIMIT)
            except OSError:
                return UNRESOLVED
            if cut:
                return UNRESOLVED
            self.scan(_script_text(body), source)  # o que o script cita conta como uso do pacote dono
            spec = _interpreter_named(name)
            imports = _python_imports(body)[0] if spec.language == "python" else []
            if spec is not SHELL and (imports is None or imports or _foreign_imports(spec.language, body)):
                return UNRESOLVED  # A não segue o import (o módulo pode citar o pacote): R7-1
            start, grammar = 2, None
        else:
            # Programa executado direto: se for script, o interpretador do ``#!`` também precisa se identificar.
            form = _worst(form, self._shebang(program, source))
            start, grammar = 1, self._grammar(name, program)
        return _worst(form, *self._arguments(words, start, path_dirs, source, wrapped, grammar))

    def _grammar(self, name, program):
        """Gramática declarada do executável, só quando o dono RPM é o pacote que ela descreve."""
        grammar = EXEC_GRAMMARS.get(name)
        if grammar is None:
            return None
        if self.owners.package(program) != grammar.package:
            owner = self.owners(program)
            self.grammar_state = ("grammar_owner_" + owner if owner in ("failed", "over_limit", "unowned")
                                  else "grammar_owner_mismatch")
            return None
        return grammar

    def _arguments(self, words, start, path_dirs, source, wrapped, grammar):
        """Argumentos de ``words`` a partir de ``start``. Com gramática, valor de dado e palavra de comando registrado
        não resolvem programa. Falha de token fora de envoltório grava o índice em ``token_failure`` (só o índice)."""
        exempt, blocking = frozenset(), frozenset()
        if grammar is not None:
            exempt, blocking = _grammar_scan(grammar, words)
            command = words[start] if len(words) > start else None
            aliased = (command is not None and grammar.alias_dir is not None
                       and not self._alias_free(command, grammar.alias_dir))
            if command in grammar.commands and not aliased:
                exempt = exempt | {start}
            elif command is not None and grammar.commands and self.grammar_state is None:
                # Motivo da palavra de comando não isenta, sem o texto dela: alias ou palavra fora da lista.
                self.grammar_state = "command_aliased" if aliased else "command_not_listed"
        statuses = []
        for index in range(start, len(words)):
            if index in blocking:
                status = UNRESOLVED
            elif index in exempt:
                status = self._payload(words[index], source)
            else:
                status = self._argument(words[index], source, path_dirs)
            if status == UNRESOLVED and not wrapped and self.token_failure is None:
                self.token_failure = index
            statuses.append(status)
        return statuses

    def _alias_free(self, word, directory):
        """``False`` quando ``directory`` (aliases.d do dnf) define a palavra, ou não pode ser lido por inteiro."""
        texts = self._alias_texts(directory)
        if texts is None:
            return False
        pattern = re.compile(r"^\s*%s\s*[=:]" % re.escape(word))
        return not any(pattern.search(line) for text in texts for line in text.splitlines())

    def _alias_texts(self, directory):
        if directory not in self.alias_cache:
            self.alias_cache[directory] = self._read_dir(directory)
        return self.alias_cache[directory]

    def _read_dir(self, directory):
        """Texto de todos os arquivos do diretório. Sem diretório, não há alias (``[]``). Listagem, leitura, arquivo
        cortado ou ilegível: ``None``, que a chamada trata como sem resolução."""
        try:
            names = self.fs.listdir(directory)
        except FileNotFoundError:
            return []
        except OSError:
            return None
        texts = []
        for name in names:
            try:
                text, cut = self.fs.read(directory.rstrip("/") + "/" + name, FILE_LIMIT)
            except OSError:
                return None
            if cut:
                return None
            texts.append(text)
        return texts

    def _payload(self, word, source):
        """Valor que a gramática declara como dado: não roda programa. Caminho limpo conta como uso do pacote dono."""
        if _clean_path(word):
            self.matches.add(word, source=source)
        return RESOLVED

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
            if self._coreutils_applet(interp, args, path):
                return RESOLVED
        return _worst(*[self._argument(arg, source, CRON_DEFAULT_PATH) for arg in args])

    def _coreutils_applet(self, interp, args, path):
        """``#!/usr/bin/coreutils --coreutils-prog-shebang=true`` (coreutils-single): o valor só escolhe o applet do
        próprio binário (``src/coreutils.c``), nunca roda outro programa. Vale só com o nome do script e o dono RPM."""
        return (interp.rsplit("/", 1)[-1] == "coreutils" and self.owners.package(interp) in COREUTILS_PACKAGES
                and args == [COREUTILS_SHEBANG + path.rsplit("/", 1)[-1]])

    def command(self, command, path_dirs, source):
        """Linha de comando: separador, expansão e aspas não se identificam; o resto segue ``resolves``."""
        cleaned = SIMPLE_REDIRECT_RE.sub(" ", command)
        if UNRESOLVED_RE.search(cleaned):
            return UNRESOLVED
        return self.resolves(cleaned.split(), path_dirs, source)

    def table(self, path, kind):
        """Um crontab: variáveis na ordem em que aparecem e cada linha de comando pela gramática de ``kind``."""
        text = self.text(path, "user_crontab_spool" if kind == "user" else None)
        if text is None:
            return
        self.matches.add(path, source=path)
        self.scan(text, path)
        path_dirs, safe = CRON_DEFAULT_PATH, True
        for number, raw in enumerate(text.splitlines(), 1):
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
                self.outcome(UNRESOLVED, path, number)
            else:
                self.outcome(self.command(command, path_dirs, path), path, number)

    def tables(self):
        for path in CRON_FILES:
            if self.expired():
                return
            self.table(path, "ana" if path.endswith("anacrontab") else "system")
        for directory, kind in zip(CRON_TABLE_DIRS, ("system", "user")):
            spool = "user_crontab_spool" if kind == "user" else None
            for path in self.listing(directory, spool):
                if self.expired():
                    self.spool_gap(spool)
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
                self.outcome(_worst(owned, self._shebang(self.paths.final(path), path)), path)
                text = self.text(path)
                if text is not None:
                    self.scan(_script_text(text), path)

    def at_jobs(self):
        for path in self.listing(AT_SPOOL, "at_jobs"):
            if self.expired():
                self.spool_gap("at_jobs")
                return
            text = self.text(path, "at_jobs")
            if text is None:
                continue
            # Job de at é o shell do usuário mais o comando: não há programa identificável.
            self.counts["entries"] += 1
            self.outcome(UNRESOLVED, path)
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
                    # ambiente que a sonda não lê ou que muda o que o programa carrega
                    self.outcome(UNRESOLVED, source,
                                 detail="environment_file" if key == "EnvironmentFiles" else "risky_environment")
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
            self.outcome(UNRESOLVED, source, detail="exec_line_unparsed")
            return
        program, argv = match.group(1), match.group(2)
        self.scan(argv, source)
        words = argv.split()
        if UNRESOLVED_RE.search(argv) or not words:
            self.outcome(UNRESOLVED, source, detail="argv_unresolved")
            return
        self.token_failure, self.grammar_state = None, None
        status = self.resolves([program] + words[1:], SYSTEMD_DEFAULT_PATH, source)
        detail = None
        if status == UNRESOLVED:
            detail = ("command_unresolved" if self.token_failure is None
                      else "token_unresolved:%d" % self.token_failure)
            if self.token_failure is not None and self.grammar_state is not None:
                detail += ":" + self.grammar_state
        self.outcome(status, source, detail=detail)


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
                         for key in ("files", "entries", "timers", "unresolved", "not_allowlisted")},
              "unresolved_entries": schedules.unresolved.items,
              "unresolved_entries_over_limit": schedules.unresolved.over,
              # A N5 fecha o spool de B pela origem: com root, A leu tudo e nenhuma entrada cita a raiz.
              "spools": schedules.spool_summary()}
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
        # Dono dos matches com orçamento próprio: não consome o das dimensões, então o veredito não muda.
        paths = _Paths(fs, _Owners(run))
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


def _names_match(values, pattern):
    return (isinstance(values, list) and len(values) <= MAX_CHECK_ITEMS
            and all(isinstance(value, str) and pattern.fullmatch(value) for value in values))


def _successor_request(request):
    """``successor_runtime`` do request da candidata B: ``None`` sem pedido, ``[]`` com pedido inválido."""
    if "successor_runtime" not in request:
        return None
    checks = request["successor_runtime"]
    if not isinstance(checks, list) or not 0 < len(checks) <= MAX_SUCCESSOR_CHECKS:
        return []
    for check in checks:
        if not isinstance(check, dict) or not _names_match([check.get("package")], PACKAGE_RE):
            return []
        if check.get("path_owner") is True:
            # Dono do caminho: sem sucessor nomeado; B mede o dono de cada caminho pedido (reference_owners).
            if check.get("successor") is not None:
                return []
        elif not _names_match([check.get("successor")], PACKAGE_RE):
            return []
        if not all(_names_match(check.get(key, []), pattern) for key, pattern in (
                ("units", UNIT_RE), ("reference_paths", PATH_RE), ("requirers", PACKAGE_RE))):
            return []
    return checks


RPM_VERIFY_FLAGS_RE = re.compile(r"^([.SM5DLUGTP?]{9}|missing)\s+([cdglr]?)\s*(/.*)$")
#: Flag do ``rpm -V`` que muda o arquivo (tamanho, modo, digest, dispositivo, link, dono, grupo, capacidades) ou que o
#: rpm não conseguiu conferir (``?``). Só o mtime (``T``) não muda o conteúdo assinado: o ``.repo`` do RHUI na AWS vem
#: com ``.......T.`` e é o arquivo íntegro do fornecedor.
RPM_VERIFY_CONTENT_FLAGS = frozenset("SM5DLUGP?")


def _content_changed(verified, path):
    """O ``rpm -V`` mostra o arquivo ausente ou com conteúdo, tamanho, modo, dono ou link diferente do pacote."""
    flags = (verified.get(path) or (None,))[0]
    return flags is not None and (flags == "missing" or any(flag in RPM_VERIFY_CONTENT_FLAGS for flag in flags))
RPM_KEY_RE = re.compile(r"Key ID ([0-9a-f]{16})")
PGP_ARMOR_RE = re.compile(r"-----BEGIN PGP PUBLIC KEY BLOCK-----(.*?)-----END PGP PUBLIC KEY BLOCK-----", re.S)
MAX_VENDOR_UNDECIDABLE = 500
# Ação do item no histórico do dnf (libdnf ``TransactionItemAction``) que põe o pacote no rpmdb; estado 1 = feito.
HISTORY_INSTALL_ACTIONS = (1, 2, 6, 9)
REPO_DIR = "/etc/yum.repos.d"
VENDOR_QUERY = ("%{NAME}|%{EPOCHNUM}|%{VERSION}|%{RELEASE}|%{ARCH}|%{RSAHEADER:pgpsig}|%{DSAHEADER:pgpsig}|"
                "%{SIGPGP:pgpsig}\\n")


def _pgp_key_ids(armored):
    """Fingerprint V4 (SHA-1 do pacote de chave) da chave primária e das subchaves de cada bloco armado; a Key ID de 64
    bits é o fim dela. Pacote que não se lê não entra (a chave fica não medida)."""
    found = {}
    for block in PGP_ARMOR_RE.findall(armored or ""):
        lines = [line.strip() for line in block.strip().splitlines()]
        body = [line for line in lines[lines.index(""):] if line and not line.startswith("=")] if "" in lines else [
            line for line in lines if line and ":" not in line and not line.startswith("=")]
        try:
            data = base64.b64decode("".join(body))
        except (ValueError, TypeError):
            continue
        position = 0
        while position < len(data):
            octet = data[position]
            if not octet & 0x80:
                break
            if octet & 0x40:
                tag, position = octet & 0x3F, position + 1
                if position >= len(data):
                    break
                first = data[position]
                if first < 192:
                    length, position = first, position + 1
                elif first < 224 and position + 1 < len(data):
                    length, position = ((first - 192) << 8) + data[position + 1] + 192, position + 2
                elif first == 255 and position + 4 < len(data):
                    length, position = int.from_bytes(data[position + 1:position + 5], "big"), position + 5
                else:
                    break
            else:
                tag, kind = (octet >> 2) & 0x0F, octet & 0x03
                size = {0: 1, 1: 2, 2: 4}.get(kind)
                if size is None or position + size >= len(data):
                    break
                length, position = int.from_bytes(data[position + 1:position + 1 + size], "big"), position + 1 + size
            packet = data[position:position + length]
            position += length
            if tag in (6, 14) and packet[:1] == b"\x04":
                fingerprint = hashlib.sha1(b"\x99" + len(packet).to_bytes(2, "big") + packet).hexdigest()
                found[fingerprint[-16:]] = fingerprint
    return found


class _VendorTrust:
    """Limite de confiança por medição (decisão do orquestrador, v6): arquivo de pacote do fornecedor do destino,
    assinado pela chave da release de B (a que assina o pacote ``system-release``, importada no rpmdb), instalado de
    repositório do fornecedor e íntegro pelo ``rpm -V``. Tudo medido em B; falta uma medida, o arquivo fica no estrito."""

    def __init__(self, run, fs, owners, paths, history_path=None):
        self.run, self.fs, self.owners, self.paths = run, fs, owners, paths
        self.history_path = history_path or DNF_HISTORY
        self._release, self._packages, self._verify, self._history, self._repos = None, {}, {}, None, None
        self._foreign = None

    def release(self):
        """Pacote ``system-release``, versão, chave que o assina e se a chave está importada (``gpg-pubkey``)."""
        if self._release is None:
            self._release = {"package": None, "version": None, "key": None, "key_imported": False}
            found = self.run(["rpm", "-q", "--whatprovides", "system-release", "--qf", "%{NAME}\n"])
            names = found["stdout"].split() if found["rc"] == 0 and not found["truncated"] else []
            if len(names) == 1:
                info = self.package(names[0])
                if info:
                    keys = self.imported_keys()
                    self._release = {"package": info["name"], "version": info["version"], "key": info["key"],
                                     "key_fingerprint": keys.get(info["key"]),
                                     "key_imported": bool(info["key"]) and info["key"] in keys}
        return self._release

    def imported_keys(self):
        """Key ID de 64 bits -> fingerprint de cada chave importada no rpmdb (``gpg-pubkey``), pelo bloco armado."""
        shown = self.run(["rpm", "-q", "gpg-pubkey", "--qf", "%{DESCRIPTION}\n"])
        if shown["rc"] != 0 or shown["truncated"]:
            return {}
        return _pgp_key_ids(shown["stdout"])

    def package(self, name):
        """NEVRA e chave que assinou o pacote instalado (uma instância só); ``None`` quando não se mede."""
        if name not in self._packages:
            shown = self.run(["rpm", "-q", "--qf", VENDOR_QUERY, "--", name])
            lines = [line for line in shown["stdout"].splitlines() if line.strip()]
            info = None
            if shown["rc"] == 0 and not shown["truncated"] and len(lines) == 1 and lines[0].count("|") == 7:
                fields = lines[0].split("|")
                keys = {match for field in fields[5:] for match in RPM_KEY_RE.findall(field)}
                info = {"name": fields[0], "epoch": fields[1], "version": fields[2], "release": fields[3],
                        "arch": fields[4], "key": keys.pop() if len(keys) == 1 else None,
                        "nevra": "%s-%s:%s-%s.%s" % tuple(fields[:5])}
            self._packages[name] = info
        return self._packages[name]

    def verify(self, name):
        """``rpm -V`` do pacote: ``{caminho: (flags, atributo)}`` do que diverge; ``None`` quando não se lê."""
        if name not in self._verify:
            shown = self.run(["rpm", "-V", "--nodeps", "--noscripts", "--", name])
            result = None
            if shown["rc"] in (0, 1) and not shown["truncated"]:
                result = {}
                for line in shown["stdout"].splitlines():
                    match = RPM_VERIFY_FLAGS_RE.match(line.strip())
                    if match:
                        result[_usrmerge(match.group(3))] = (match.group(1), match.group(2))
                    elif line.strip():
                        result = None
                        break
            self._verify[name] = result
        return self._verify[name]

    def history(self):
        """Repositório e ``releasever`` de cada NEVRA posto no rpmdb por uma transação feita do dnf."""
        if self._history is None:
            self._history = {}
            try:
                import sqlite3
                connection = sqlite3.connect("file:%s?mode=ro" % self.history_path, uri=True)
            except Exception:  # noqa: BLE001 — sem módulo ou sem arquivo legível: repositório não medido
                return self._history
            try:
                rows = connection.execute(
                    "SELECT rpm.name, rpm.epoch, rpm.version, rpm.release, rpm.arch, repo.repoid, trans.releasever, "
                    "trans.id FROM trans_item JOIN rpm ON rpm.item_id = trans_item.item_id "
                    "JOIN repo ON repo.id = trans_item.repo_id JOIN trans ON trans.id = trans_item.trans_id "
                    "WHERE trans_item.state = 1 AND trans_item.action IN (%s) ORDER BY trans.id"
                    % ",".join(str(action) for action in HISTORY_INSTALL_ACTIONS)).fetchall()
            except Exception:  # noqa: BLE001 — esquema diferente ou banco travado
                rows = []
            finally:
                connection.close()
            for name, epoch, version, release, arch, repoid, releasever, trans in rows:
                nevra = "%s-%s:%s-%s.%s" % (name, epoch or 0, version, release, arch)
                self._history[nevra] = {"repository": repoid, "release": releasever, "transaction": trans}
        return self._history

    def foreign_repositories(self):
        """Seções dos ``.repo`` de terceiro: arquivo sem dono, de pacote que a chave da release não assina, ou alterado
        no ``rpm -V``. Repositório sem ``.repo`` (o do build da imagem, ``@commandline``) não é identificável."""
        if self._foreign is None:
            self._foreign = set()
            try:
                names = self.fs.listdir(REPO_DIR)
            except OSError:
                return self._foreign
            release = self.release()
            for name in names:
                path = REPO_DIR + "/" + name
                if not name.endswith(".repo"):
                    continue
                owner = self.owners.package(path)
                info = self.package(owner) if owner else None
                verified = self.verify(owner) if owner else None
                if (info and info["key"] == release["key"] and verified is not None
                        and not _content_changed(verified, _usrmerge(path))):
                    continue  # ``.repo`` íntegro do fornecedor
                try:
                    text, _ = self.fs.read(path, FILE_LIMIT)
                except OSError:
                    continue
                self._foreign.update(match.group(1).strip() for match in re.finditer(r"(?m)^\[([^\]]+)\]", text))
        return self._foreign

    def vendor_repositories(self):
        """Seções dos ``.repo`` que são arquivo íntegro de pacote assinado pela chave da release."""
        if self._repos is None:
            self._repos = set()
            try:
                names = self.fs.listdir(REPO_DIR)
            except OSError:
                return self._repos
            release = self.release()
            for name in names:
                path = REPO_DIR + "/" + name
                if not name.endswith(".repo"):
                    continue
                owner = self.owners.package(path)
                info = self.package(owner) if owner else None
                verified = self.verify(owner) if owner else None
                if (not info or info["key"] != release["key"] or verified is None
                        or _content_changed(verified, _usrmerge(path))):
                    continue
                try:
                    text, _ = self.fs.read(path, FILE_LIMIT)
                except OSError:
                    continue
                self._repos.update(match.group(1).strip() for match in re.finditer(r"(?m)^\[([^\]]+)\]", text))
        return self._repos

    def trust(self, path):
        """``(registro, None)`` para arquivo do fornecedor íntegro; ``(None, motivo)`` quando fica no estrito."""
        real = self.paths.final(path)
        owner = self.owners.package(real)
        if owner is None:
            return None, "unowned"
        info = self.package(owner)
        if info is None:
            return None, "package_unmeasured"
        release = self.release()
        if not release["key"] or not release["key_imported"]:
            return None, "release_key_unmeasured"
        if info["key"] is None:
            return None, "package_unsigned"
        if info["key"] != release["key"]:
            return None, "key_not_release"
        verified = self.verify(owner)
        if verified is None:
            return None, "rpm_verify_unmeasured"
        if _content_changed(verified, _usrmerge(real)):
            return None, "file_modified"
        # Repositório de origem: gravado, não exigido (a assinatura pela chave da release é a prova). Assinado pela
        # chave do fornecedor mas instalado de repositório de terceiro é contradição: fica no estrito.
        origin = self.history().get(info["nevra"]) or {}
        repository = origin.get("repository")
        if repository is not None and repository in self.foreign_repositories():
            return None, "vendor_signature_foreign_repo"
        state = ("vendor" if repository in self.vendor_repositories() else
                 "unmeasured" if repository is None else "unidentified")
        return {"path": path, "resolved_path": real, "package": owner, "nevra": info["nevra"], "key": info["key"],
                "key_fingerprint": release.get("key_fingerprint"), "rpm_verify": "clean",
                "repository": repository, "repository_state": state, "release": origin.get("release")}, None

    def modified_config(self, owner):
        """Arquivos ``%config`` do pacote que o ``rpm -V`` mostra alterados (parte do workload)."""
        return sorted(path for path, (flags, kind) in (self.verify(owner) or {}).items()
                      if kind == "c" and flags != "missing")


class _SuccessorScan:
    """O que B mostra de um sucessor: unidades, programas e ``#!``, executáveis dos requerentes e crontabs do sistema.

    Só lê. Cada leitura que falha entra em ``reasons`` e deixa a cobertura ``partial``; nada vira ausência em silêncio.
    """

    def __init__(self, run, fs, deadline, stream=None):
        self.run, self.fs, self.deadline = run, fs, deadline
        self.stream = stream or stream_lines
        self.owners, self.paths = _Owners(run), _Paths(fs)
        self.reasons, self.scanned, self.cron, self.others = set(), collections.defaultdict(set), None, None
        self.unread = set()  # fonte que pode citar a raiz e não foi lida (sem permissão ou sem lista): ``limit``
        self.refs, self.names, self.found = frozenset(), {}, []
        self.undecidable, self.undecidable_over_limit = [], 0
        self.word_occurrences = {}
        self.resolved, self.envs = [], {}
        self.python_contexts, self.python_cache = {}, {}  # sys.path medido e módulo lido valem para todas as checagens
        self.loaded_cited = set()
        self.via_stack, self.roots, self.exec_seen, self.exec_count = [], {}, set(), 0
        self.executed_binaries = []
        self.touched, self.exec_children, self.exec_cache = [], {}, {}  # fontes tocadas, por programa (R10-1)
        self.dead_command_branches = []
        self.python_deadline = None
        self.python_limits = {"module_cap": PY_IMPORT_MAX_MODULES, "depth_cap": PY_IMPORT_MAX_DEPTH,
                              "time_budget_s": PY_IMPORT_TIME_BUDGET_S, "over_limit": False, "time_budget_hit": False}
        self.python_cited, self.python_receipt, self.python_scanned = set(), [], 0
        self.glob_expansions, self.dead_branches, self.decided_evals, self.glob_seen = [], [], [], set()
        self.trust = _VendorTrust(run, fs, self.owners, self.paths)
        self.vendor_undecidable, self.vendor_undecidable_over_limit = [], 0
        self.vendor_trusted, self.trust_refused, self.workload_scanned = [], [], []
        self.workload_seen, self.scripts = set(), {}
        self.interpreter_scripts, self.interpreter_seen, self.interpreter_contexts = {}, set(), set()
        self.loader_conf_read, self.loader_conf = False, None  # ``/etc/ld.so.conf`` lido uma vez por coleta

    def expired(self):
        if time.monotonic() > self.deadline:
            self.reasons.add("time_budget")
            return True
        return False

    def rooted(self, source):
        """Contexto em que ``source`` é a origem raiz das leituras que ele dispara (módulo, script, arquivo carregado,
        programa executado): cada fonte lida dentro dele grava essa origem (``via``)."""
        scan = self

        class _Root(object):
            def __enter__(self):
                scan.via_stack.append(scan.via_stack[0] if scan.via_stack else source)
                scan.mark_root(source)

            def __exit__(self, *exc):
                scan.via_stack.pop()
                return False
        return _Root()

    def mark_root(self, source):
        self._add_root(source, self.via_stack[0] if self.via_stack else source)

    def _add_root(self, source, root):
        """Grava ``root`` como origem de ``source`` e marca a fonte como tocada na janela do programa que a alcançou."""
        self.roots.setdefault(source, set()).add(root)
        self.touched.append(source)

    def cite_literal(self, text, source):
        """Só a referência literal (caminho ou nome da raiz) do texto, sem a análise de indecidível."""
        self.mark_root(source)
        for token in TOKEN_RE.findall(text):
            hits = [_usrmerge(_normal_path(path)) for path in _token_paths(token)] if "/" in token else [
                self.names.get(token)]
            for reference in hits:
                if reference in self.refs and {"source": source, "reference": reference} not in self.found:
                    self.found.append({"source": source, "reference": reference})

    def cite(self, text, source, shell=None, extra=()):
        with self.rooted(source):
            self._cite(text, source, shell, extra)

    def _cite(self, text, source, shell=None, extra=()):
        """Caminho de referência da raiz citado no texto (normalizado e pelo usrmerge: ``//usr/bin/python3.6`` também),
        ou o nome de um comando dela num diretório do PATH chamado sem caminho (``python3.6 x.py``): achado com a fonte
        e o caminho, sem o texto. O que o texto pode rodar sem que se saiba o quê vai para ``undecidable``."""
        for token in TOKEN_RE.findall(text):
            if "/" in token:
                hits = [_usrmerge(_normal_path(path)) for path in _token_paths(token)]
            else:
                hits = [self.names.get(token)]
            for reference in hits:
                if reference in self.refs and {"source": source, "reference": reference} not in self.found:
                    self.found.append({"source": source, "reference": reference})
        self.undecided(text, source, shell, extra)

    def undecided(self, text, source, shell=None, extra=()):
        with self.rooted(source):
            self._undecided(text, source, shell, extra)

    def _undecided(self, text, source, shell=None, extra=()):
        """Referência indecidível: expansão (``$VAR``, ``${...}``) que cita o nome ou o caminho da raiz, ou que está na
        posição de comando de texto de shell (``${PYTHON:-python3.6} x.py``, ``"$PY" x.py``, ``eval "$CMD"``). A sonda não
        avalia o shell: registra a fonte, o motivo e a palavra (até 120 caracteres; vinda de arquivo carregado, também o
        arquivo e a linha) e a N5 conta como ``not_proven``."""
        names = set(self.names) | {ref.rsplit("/", 1)[1] for ref in self.refs}
        reasons, details = set(), []
        for token in TOKEN_RE.findall(text):
            if ("$" in token or "`" in token) and (any(name in token for name in names)
                                                    or any(ref in _normal_path(token) for ref in self.refs)):
                entry = {"source": source, "reason": "expansion_names_root", "word": token[:120]}
                if entry not in details and len(details) < MAX_CHECK_ITEMS:
                    details.append(entry)
        if source.startswith("unit:"):
            pass  # Exec*: o argv vem do D-Bus por elemento (``exec_command``); não é texto de shell
        elif shell or (shell is None and _shell_text(text)):
            details.extend(self.resolve_command_words(text, source))
        details.extend(extra)
        keep = self.root_tokens()
        pending = [self.redact_entry(entry, keep) for entry in
                   [{"source": source, "reason": reason} for reason in sorted(reasons)] + details]
        if pending:
            pending = self.vendor_file(source, text, pending)
        self.undecidable_add(pending)

    def resolve_command_words(self, text, source, environments=None):
        """Variável na posição de comando decidida pelo resolvedor estrito (``_ShellScript``). Decidida, cada valor passa
        pela normalização e pela busca da raiz: valor que cita a raiz é referência. Indecidível, volta a entrada com o
        motivo do contrato. O decidido vai para ``resolved_command_variables`` com fontes e referências da raiz; o que se
        decidiu por medição (glob, ramo morto, ``eval`` literal) vai com a fonte para o recibo."""
        kind = _cron_kind(source)
        if kind:
            # Linha de crontab: os campos de horário (e o usuário) vêm antes do comando; ``NOME=valor`` fica.
            text = "\n".join((_cron_command(line.strip(), kind) or line) if line.strip() else ""
                             for line in text.splitlines())
        origin = source.split(":", 1)[1] if source.startswith(("program:", "shebang:", "script:", "exec:")) else source
        entries = []
        if environments is None:
            environments = self.envs.get(source, [None] if source.startswith("shebang:") else [{}])
        for env in environments:
            script = _ShellScript(text, origin, env, self.fs)
            self.scripts.setdefault(source, []).append(script)
            if not script.balanced:
                line, word = script.unbalanced or (None, "")
                entries.append({"source": source, "reason": "shell_quotes_unbalanced", "line": line,
                                "word": word or "<<"})
                continue
            for item in script.substitution_words:
                entry = {"source": source, "reason": "expansion_in_command_position", "detail": "command_substitution",
                         "variable": None, "origin": origin, "line": item["line"], "word": item["word"]}
                if item["origin"] != origin:
                    entry["word_origin"], entry["word_line"] = item["origin"], item["origin_line"]
                if entry not in entries:
                    entries.append(entry)
            entries.extend(self.record_measured(source, script))
            for loaded in script.loaded:
                # Arquivo carregado com ``.``: o resolvedor já o leu junto; a referência literal dele também conta.
                if loaded["path"] not in self.loaded_cited:
                    self.loaded_cited.add(loaded["path"])
                    try:
                        content, _ = self.fs.read(loaded["path"], FILE_LIMIT)
                    except OSError:
                        continue
                    self.cite_literal(_script_text(content), "loaded:" + loaded["path"])
            for item in script.command_items():
                if item["kind"] == "interpreter":
                    entries.extend(self.interpreter_item(source, script, item, origin))
                    continue
                if item["kind"] == "executed":
                    entries.extend(self.exec_program(item["text"], source, item["line"]))
                    continue
                try:
                    if item["kind"] == "undecided":
                        raise item["error"]
                    if item["kind"] == "none":
                        values = set()
                    elif item["kind"] == "value":
                        values = {item["text"]}
                    else:
                        values = script.evaluate(item["raw"], item["line"], item["function"], 0)
                    self.record_resolved(source, script, item, values)
                    if item.get("place") == "command":
                        script.decided_paths.update(_normal_path(value.split()[0]) for value in values
                                                    if value.split() and value.split()[0].startswith("/"))
                    if item.get("place") == "command" and "" in values:
                        raise _Undecided("empty_command_value", None, origin)
                    if item.get("place") == "command":
                        entries.extend(self.glob_programs(source, script, values))
                        globbed = {entry["path"] for record in script.globs for entry in record["files"]}
                        for value in sorted(values):
                            head = value.split()[0] if value.split() else ""
                            if EXECUTED_PATH_RE.fullmatch(head) and head not in globbed:  # o do glob já foi lido acima
                                entries.extend(self.exec_program(head, source, item["line"]))
                except _Undecided as exc:
                    entry = {"source": source, "reason": "expansion_in_command_position", "detail": exc.reason,
                             "variable": exc.variable, "origin": exc.origin, "line": item["line"],
                             "word": (script._restore(item["raw"]) if item.get("raw") else item.get("text") or "")[:120]}
                    if exc.origin_line is not None:
                        entry["origin_line"] = exc.origin_line
                    wrapper = exc.wrapper or (item.get("wrapper") or [None])[-1]
                    if wrapper:
                        entry["wrapper"] = wrapper
                    if item["origin"] != origin:
                        entry["word_origin"], entry["word_line"] = item["origin"], item["origin_line"]
                    entries.append(entry)
        return entries

    def interpreter_item(self, source, script, item, origin):
        """Interpretador chamado no texto de shell: o argv literal (aspas tiradas) vai para ``interpreter_target``;
        argumento com expansão que não dá uma palavra só fica indecidível no ponto em que a gramática precisa dele."""
        argv, tokens, index = [item["text"]], item["tokens"], 0
        while index < len(tokens):
            width = script._redirect(tokens, index)
            if width:
                index += width
                continue
            token = tokens[index]
            index += 1
            if token[0] == "text":
                argv.append(token[1])
                continue
            try:
                fields = script._fields(token, item["line"], item["function"])
            except _Undecided:
                fields = None
            if fields is not None and len(fields) == 1 and len(fields[0]) == 1:
                argv.append(fields[0][0][1])
            else:
                argv.append("$" + script._restore(token[1]))  # fica com ``$``: a gramática a trata como indecidível
        found = self.interpreter_target(item["text"], argv, source, script.env)
        for entry in found:
            entry["line"] = item["line"]
            if item["origin"] != origin:
                entry["word_origin"], entry["word_line"] = item["origin"], item["origin_line"]
        return found

    # ── R7-1: imports do script lido pelo interpretador. ──

    def script_imports(self, interp, flags, script, text, source, envs):
        """Python: imports resolvidos apenas quando o contexto isolado equivale ao da entrada observada;
        cada módulo resolvido passa pelo mesmo contrato, com teto. Perl, Ruby e
        Node: import detectado fica ``interpreter_imports_not_analyzed``."""
        spec = _interpreter_named(self.paths.final(interp).rsplit("/", 1)[-1])
        if spec is None:
            return []
        if spec.language != "python":
            if _foreign_imports(spec.language, text):
                return [_undecided_entry(source, "interpreter_imports_not_analyzed", spec.language, origin=script)]
            return []
        if flags is None:
            return [_undecided_entry(source, "python_startup_options_unproven", interp, origin=script)]
        entries = []
        for env in envs or [None]:
            for entry in self.python_program(interp, flags, script, text, source, env):
                if entry not in entries:
                    entries.append(entry)
        return entries

    def python_environment(self, flags, env):
        """Ambiente PYTHON* que muda o ``sys.path``: ``{}`` com ``-E``/``-I``; ``None`` quando não se lê ou é ambíguo."""
        if env is None:
            return None
        chosen = {}
        names = PY_STARTUP_ENVIRONMENT
        if "E" not in flags and "I" not in flags:
            names += PY_ENVIRONMENT
        for name in names:
            values = env.get(name)
            if values:
                if len(values) != 1:
                    return None
                chosen[name] = next(iter(values))
        return chosen

    def python_startup_unproven(self, interp, executable):
        # Não lê o conteúdo de configuração que seleciona outro runtime. Presença ou leitura incerta basta.
        directories = {path.rsplit("/", 1)[0] for path in (interp, executable)}
        if any(_usrmerge(directory + "/") not in ("/usr/bin/", "/usr/libexec/") for directory in directories):
            return "interpreter_location_unproven"
        config = {path + "._pth" for path in (interp, executable)}
        for directory in directories:
            config.update((directory + "/pyvenv.cfg", directory.rsplit("/", 1)[0] + "/pyvenv.cfg"))
        for path in sorted(config):
            try:
                self.fs.read(path, 1)
            except FileNotFoundError:
                continue
            except OSError:
                return "python_path_configuration_unproven"
            return "python_path_configuration_unproven"
        for directory in sorted(directories | {"/usr/lib", "/usr/lib64", "/lib", "/lib64"}):
            try:
                names = self.fs.listdir(directory)
            except FileNotFoundError:
                continue
            except OSError:
                return "python_path_configuration_unproven"
            if any(name.startswith("libpython") and name.endswith("._pth") for name in names):
                return "python_path_configuration_unproven"
        try:
            preload, cut = self.fs.read("/etc/ld.so.preload", FILE_LIMIT)
        except FileNotFoundError:
            return None
        except OSError:
            return "global_loader_preload_unproven"
        if preload.strip() or cut:
            return "global_loader_preload_unproven"
        return None

    @staticmethod
    def parse_python_context(shown):
        output = shown.get("stdout", "")
        if shown.get("rc") != 0 or shown.get("truncated") or len(output.encode("utf-8", "replace")) > PY_CONTEXT_MAX_BYTES:
            return None
        if len(output.splitlines()) != 1:
            return None
        try:
            context = ast.literal_eval(output.strip())
        except (ValueError, SyntaxError, RecursionError):
            return None
        if not isinstance(context, dict) or set(context) != PY_CONTEXT_FIELDS:
            return None
        if context["base_installation"] is not True or context["isolated_startup"] is not True:
            return None
        for name in ("path", "builtin", "suffixes", "frozen"):
            if not isinstance(context[name], list) or not all(isinstance(value, str) for value in context[name]):
                return None
        if not context["path"] or not all(path.startswith("/") for path in context["path"]):
            return None
        return context

    def python_context(self, interp, flags, environment, script=None):
        """Mede sem hooks e só admite equivalência quando o workload também desliga site."""
        script_dir = None
        if "I" not in flags and script and script.startswith("/"):
            try:
                resolved_script = self.fs.realpath(script)
            except OSError:
                resolved_script = ""
            if resolved_script.startswith("/"):
                script_dir = resolved_script.rsplit("/", 1)[0] or "/"
        key = (interp, flags, tuple(sorted(environment.items())), script_dir)
        if key not in self.python_contexts:
            context, reason = None, None
            executable = self.paths.final(interp)
            if environment:
                reason = "python_environment_unproven"
            elif "S" not in flags:
                reason = "site_startup_unproven"
            elif "I" not in flags and script_dir is None:
                reason = "script_path_unproven"
            else:
                reason = self.python_startup_unproven(interp, executable)
            if reason is None:
                shown = self.run(["env", "-i", "PATH=/usr/bin:/bin", executable, "-I", "-S", "-B", "-c", PY_MEASURE_CODE])
                context = self.parse_python_context(shown)
                if context is None:
                    reason = "isolated_context_unproven"
                elif script_dir is not None:
                    context["path"] = [script_dir] + context["path"]
            self.python_contexts[key] = context
            if len(self.python_receipt) < MAX_RESOLVED_ENTRIES:
                self.python_receipt.append({"interpreter": interp, "flags": flags,
                                            "environment": {name: True for name in sorted(environment)},
                                            "sys_path": (context or {}).get("path"), "measured": context is not None,
                                            "reason": reason})
        return self.python_contexts[key]

    def python_program(self, interp, flags, script, text, source, env):
        environment = self.python_environment(flags, env)
        if environment is None:
            return [_undecided_entry(source, "python_environment_not_read", interp, origin=script)]
        if self.trust.trust(self.paths.final(interp))[0] is None:
            # R11-1: a sonda só executa interpretador de pacote do fornecedor íntegro e assinado; o resto nunca roda.
            return [_undecided_entry(source, "python_sys_path_not_measured_untrusted_interpreter", interp, origin=script)]
        context = self.python_context(interp, flags, environment, script)
        if context is None:
            return [_undecided_entry(source, "python_sys_path_unmeasured", interp, origin=script)]
        dirs = context["path"]
        root_entries, by_module, modules, extensions = [], {}, [], []

        def flag(origin, reason, word, line=None, path=None):
            entry = _undecided_entry(source if origin is None else "module:" + origin, reason, word,
                                     origin=path or origin or script, line=line)
            (root_entries if origin is None else by_module.setdefault(origin, [])).append(entry)

        imports, undecided, _ = _python_imports(text)
        for reason, word, line in undecided:
            flag(None, reason, word, line)
        queue = [(module, level, names, line, None, 1, None) for module, level, names, line in imports or ()]
        seen, over, timed_out = set(), False, False
        if self.python_deadline is None:
            self.python_deadline = time.monotonic() + PY_IMPORT_TIME_BUDGET_S
        # Fase 1: o fechamento dos imports (só leitura e ``ast``).
        while queue:
            if self.expired():
                break
            if time.monotonic() > self.python_deadline:
                timed_out = True  # orçamento dos imports acabou: o resto do fechamento não foi lido
                break
            module, level, names, line, package, depth, origin = queue.pop(0)
            absolute = self.python_absolute(module, level, package)
            if absolute is None:
                flag(origin, "python_import_unresolved", "." * level + module, line)
                continue
            targets = ([absolute] if absolute else []) + [
                (absolute + "." + name) if absolute else name for name in names if name != "*"]
            for index, target in enumerate(targets):
                found = self.python_resolve(target, dirs, context)
                if found[0] == "missing":
                    if index == 0:
                        flag(origin, "python_import_unresolved", target, line)
                    continue  # ``from X import nome``: o nome é atributo de X, não submódulo
                if found[0] in ("builtin", "namespace") or found[1] in seen:
                    continue
                if depth > PY_IMPORT_MAX_DEPTH or len(seen) >= PY_IMPORT_MAX_MODULES:
                    over = True
                    continue
                kind, path = found
                seen.add(path)
                if kind == "extension":
                    extensions.append((path, target, origin))
                    continue
                if kind in ("zip", "compiled"):
                    flag(origin, "python_module_not_readable", target, line, path)
                    continue
                parsed = self.python_parse(path, target)
                if parsed is None:
                    flag(origin, "python_module_unreadable", target, line, path)
                    continue
                inner_imports, inner_undecided, exported, package_name, _ = parsed
                modules.append(path)
                for reason, word, inner_line in inner_undecided:
                    flag(path, reason, word, inner_line)
                if "*" in names and index == 0 and path.endswith("/__init__.py"):
                    if exported is None:
                        flag(path, "python_star_import_dynamic", target)
                    else:
                        queue.extend((target + "." + name, 0, (), line, None, depth + 1, path) for name in exported)
                queue.extend((item[0], item[1], item[2], item[3], package_name, depth + 1, path)
                             for item in inner_imports)
        if over:
            self.python_limits["over_limit"] = True
            flag(None, "python_import_over_limit", interp)
        if timed_out:
            self.python_limits["time_budget_hit"] = True
            flag(None, "python_import_time_budget", interp)
        # Fase 2: dono RPM de todos os arquivos numa leitura só (a regra do fornecedor consulta por arquivo).
        self.owners.prefetch(modules + [path for path, _, _ in extensions])
        # Fase 3: o mesmo contrato em cada módulo (referência literal, fornecedor, indecidível dele).
        for path, target, origin in extensions:
            if self.trust.trust(path)[0] is None:
                flag(origin, "python_extension_outside_vendor", target, path=path)
        for path in modules:
            self.mark_root("module:" + path)
            if path not in self.python_cited:
                self.python_cited.add(path)
                self.cite(self.python_cache[path][4], "module:" + path, shell=False, extra=by_module.pop(path, []))
        for path, entries in by_module.items():
            self.undecided("", "module:" + path, shell=False, extra=entries)
        return root_entries

    @staticmethod
    def python_absolute(module, level, package):
        """Nome absoluto do import; relativo sem pacote (script ``__main__``) ou além da raiz: ``None``."""
        if not level:
            return module
        if not package:
            return None
        parts = package.split(".")
        if level - 1 >= len(parts):
            return None
        base = ".".join(parts[:len(parts) - (level - 1)])
        return (base + "." + module) if module else base

    def python_resolve(self, name, dirs, context):
        """Como o ``PathFinder``: ``("source", arquivo)``, ``("extension", .so)``, ``("builtin", None)``,
        ``("namespace", None)``, ``("zip"|"compiled", caminho)`` ou ``("missing", None)``."""
        parts = name.split(".")
        if len(parts) == 1 and (name in (context.get("builtin") or ()) or name in (context.get("frozen") or ())):
            return ("builtin", None)
        search = list(dirs)
        for position, part in enumerate(parts):
            last = position == len(parts) - 1
            portions, hit = [], None
            for directory in search:
                if directory.endswith(".zip"):
                    if self.fs.is_file(directory):
                        hit = ("zip", directory)
                        break
                    continue
                base = directory.rstrip("/") + "/" + part
                if self.fs.is_file(base + "/__init__.py"):
                    hit = ("package", base)
                elif self.fs.is_file(base + ".py"):
                    hit = ("module", base + ".py")
                else:
                    extension = next((base + suffix for suffix in context.get("suffixes") or (".so",)
                                      if self.fs.is_file(base + suffix)), None)
                    if extension:
                        hit = ("extension", extension)
                    elif self.fs.is_file(base + ".pyc"):
                        hit = ("compiled", base + ".pyc")
                    elif self.python_dir(base):
                        portions.append(base)
                if hit:
                    break
            if hit is None:
                if portions and last:
                    return ("namespace", None)
                if portions:
                    search = portions
                    continue
                return ("missing", None)
            kind, path = hit
            if kind == "package":
                if last:
                    return ("source", path + "/__init__.py")
                search = [path]
                continue
            if not last:
                # ``import os.path``: o submódulo de um módulo comum é criado pelo código dele (``sys.modules``), que
                # a sonda já varre; a resolução é o próprio módulo.
                return ("source", path) if kind == "module" else (kind, path)
            return ("source", path) if kind == "module" else (kind, path)
        return ("missing", None)

    def python_dir(self, path):
        try:
            return stat.S_ISDIR(self.fs.mode(path))
        except (OSError, ValueError):
            return False

    def python_parse(self, path, name):
        """``(imports, indecidíveis, __all__, pacote, texto)`` do módulo; ``None`` quando não se lê."""
        if path not in self.python_cache:
            try:
                text, cut = self.fs.read(path, PY_MODULE_LIMIT)
            except OSError:
                text, cut = None, True
            if text is None or cut:
                self.python_cache[path] = None
            else:
                imports, undecided, exported = _python_imports(text)
                package = name if path.endswith("/__init__.py") else name.rpartition(".")[0]
                self.python_cache[path] = (imports or [], undecided, exported, package, text)
                self.python_scanned += 1
        return self.python_cache[path]

    def shebang_imports(self, path, source, text):
        """Programa executado direto: o interpretador do ``#!`` (e as opções dele) lê o script; os imports dele
        seguem ``script_imports`` com o ambiente das unidades que rodam o programa."""
        first = text.split("\n", 1)[0] if text else ""
        if not first.startswith("#!"):
            return
        words = first[2:].split()
        if not words or not _clean_path(words[0]):
            return
        interp, options = words[0], words[1:]
        real = self.paths.final(path)
        envs = self.shebang_environments(path, real, source)
        entries = []
        if _usrmerge(self.paths.final(interp)) == "/usr/bin/env":
            for env in envs:
                resolved = self.shebang_env_interpreter(options, env)
                if resolved is None:
                    entry = _undecided_entry(source, "interpreter_shebang_env_unproven", "env", origin=path, line=1)
                    if entry not in entries:
                        entries.append(entry)
                else:
                    entries.extend(self.script_imports(resolved, "", real, text, source, [env]))
        else:
            entries = self.script_imports(interp, _python_flags(options), real, text, source, envs)
        if entries:
            self.undecided("", source, shell=False, extra=entries)

    def shebang_environments(self, path, real, source):
        return self.envs.get("program:" + path) or self.envs.get("program:" + real) or [
            {} if source.startswith("requirer:") else None]

    def shebang_env_interpreter(self, options, env):
        # Linux entrega um único argumento opcional. A gramática de env -S não é a de argv.
        if len(options) != 1 or env is None or any(name in env for name in PY_STARTUP_ENVIRONMENT):
            return None
        return self.env_executable(options[0], env)

    def env_executable(self, named, env):
        if _clean_path(named):
            return named
        if env is None or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", named):
            return None
        values = env.get("PATH", ())
        if len(values) != 1:
            return None
        directories = next(iter(values)).split(":")
        if len(directories) > MAX_RESOLVED_ENTRIES or not all(_clean_path(d) for d in directories):
            return None
        for directory in directories:
            candidate = directory.rstrip("/") + "/" + named
            try:
                mode = self.fs.mode(candidate)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                return None
            if stat.S_ISREG(mode) and mode & 0o111:
                return candidate
        return None

    def exec_program(self, path, source, line=None):
        """Programa executado por caminho absoluto (literal ou valor decidido): script (``#!`` ou texto sem cabeçalho,
        que o shell roda) é lido pelo mesmo contrato, recursivo, com o teto e o orçamento dos imports; ELF entra só com
        dono e procedência; ausente ou ilegível é indecidível."""
        path = _normal_path(path)
        if _usrmerge(path) in self.refs:
            return []  # o caminho da raiz já é referência
        if path in self.exec_seen:
            return self._exec_replay(path, source, line)
        self.exec_seen.add(path)
        start = len(self.touched)
        entries, vendors = self._exec_once(path, source, line)
        self.exec_cache[path] = (entries, vendors)
        self.exec_children[path] = set(self.touched[start:])
        for record in vendors:
            self._vendor_binary(record, source, path, line)
        return entries

    def _exec_replay(self, path, source, line):
        """Segunda origem do mesmo programa: a leitura não se repete, mas cada fonte que a primeira leitura alcançou
        ganha esta origem (``via``), e o indecidível do programa sai de novo com a origem desta chamada (R10-1)."""
        entries, vendors = self.exec_cache[path]
        root = self.via_stack[0] if self.via_stack else source
        for child in self.exec_children.get(path, ()):
            self._add_root(child, root)
        for record in vendors:
            self._vendor_binary(record, source, path, line)
        caller = self.source_file(source) or source
        return [dict(entry, source=source, line=line,
                     origin=caller if entry["detail"].startswith("executed_program_") else entry["origin"])
                for entry in entries]

    def _vendor_binary(self, record, source, path, line):
        """ELF de pacote do fornecedor íntegro e assinado: não é varrido, mas nunca some. Entra em ``vendor_code_undecidable``
        com o motivo ``executed_binary_not_analyzed`` e o registro do fornecedor (R10-2)."""
        item = {"source": source, "file": path, "line": line, "word": path,
                "reason": "executed_binary_not_analyzed",
                "variable": None, "cause_origin": path, "cause_line": line,
                "vendor": {key: record.get(key) for key in (
                    "package", "nevra", "key", "key_fingerprint", "rpm_verify", "repository", "repository_state",
                    "release")}}
        self.redact_entry(item, self.root_tokens())
        if item in self.vendor_undecidable:
            return
        if len(self.vendor_undecidable) >= MAX_VENDOR_UNDECIDABLE:
            self.vendor_undecidable_over_limit += 1
        else:
            self.vendor_undecidable.append(item)
        self.record_vendor(record, source, "executed", [item])

    def _exec_once(self, path, source, line):
        """Leitura do programa executado: (entradas indecidíveis, registros de ELF de fornecedor a registrar)."""
        caller = self.source_file(source) or source
        if self.python_deadline is None:
            self.python_deadline = time.monotonic() + PY_IMPORT_TIME_BUDGET_S
        if self.exec_count >= PY_IMPORT_MAX_MODULES:
            return [_undecided_entry(source, "executed_program_over_limit", path, origin=caller, line=line)], []
        if time.monotonic() > self.python_deadline:
            return [_undecided_entry(source, "executed_program_time_budget", path, origin=caller, line=line)], []
        real = self.paths.final(path)
        try:
            head, _ = self.fs.read(real, SHEBANG_LIMIT)
        except OSError:
            return [_undecided_entry(source, "executed_program_unreadable", path, origin=caller, line=line)], []
        self.exec_count += 1
        if head.startswith("\x7fELF"):
            record, reason = self.trust.trust(real)
            if len(self.executed_binaries) < MAX_RESOLVED_ENTRIES:
                self.executed_binaries.append({"path": path, "owner": self.owners.package(real),
                                               "vendor": record is not None, "reason": reason})
            if record is None:  # ELF fora do fornecedor, modificado ou sem dono: estrito e indecidível (R10-2)
                return [_undecided_entry(source, "executed_binary_not_analyzed", path, origin=path, line=line)], []
            return [], [record]  # ELF do fornecedor íntegro e assinado: não é varrido, vai ao registro do fornecedor
        try:
            text, cut = self.fs.read(real, FILE_LIMIT)
        except OSError:
            return [_undecided_entry(source, "executed_program_unreadable", path, origin=caller, line=line)], []
        if cut:
            return [_undecided_entry(source, "executed_program_over_limit", path, origin=caller, line=line)], []
        if text.startswith("#!"):
            self.program(path, "exec:" + path, observed=self.shebang_text(text))
        else:
            self.cite(_script_text(text), "exec:" + path, shell=True)
        return [], []

    @staticmethod
    def source_file(source):
        """Arquivo cujo texto é a fonte (programa, ``#!``, executável do requerente, crontab, parte do workload)."""
        if source.startswith(("program:", "shebang:", "workload:", "script:", "module:", "exec:")):
            return source.split(":", 1)[1]
        if source.startswith("requirer:"):
            return source.split(":", 2)[2] if source.count(":") >= 2 else None
        return source if source.startswith("/") else None

    def vendor_file(self, source, text, pending):
        """Procedência do fornecedor medida e gravada, sem tirar nada do estrito (decisão do orquestrador, R2-1).

        O indecidível cuja palavra está em arquivo de pacote do fornecedor, assinado pela chave da release (Key ID de 64
        bits da chave importada), de repositório do fornecedor e íntegro no ``rpm -V`` (e cuja causa, quando é arquivo,
        também é) vai para ``vendor_code_undecidable`` com arquivo, linha, palavra, motivo e o registro do fornecedor:
        a N5 conta como ``not_proven`` com motivo próprio. O resto volta à lista estrita. O que o script carrega, roda
        ou cita fora desse conjunto e o ``%config`` alterado do pacote são varridos como ``workload:``."""
        path = self.source_file(source)
        if path is None or source.startswith("workload:"):
            return pending
        record, reason = self.trust.trust(path)
        if record is None:
            entry = {"path": path, "source": source, "reason": reason}
            if entry not in self.trust_refused and len(self.trust_refused) < MAX_RESOLVED_ENTRIES:
                self.trust_refused.append(entry)
            return pending
        strict, vendor = [], []
        for entry in pending:
            word_file = entry.get("word_origin") or path
            line = entry.get("word_line") if entry.get("word_origin") else entry.get("line")
            cause = entry.get("origin")
            word_record = record if word_file == path else self.trust.trust(word_file)[0]
            cause_ok = not (isinstance(cause, str) and cause.startswith("/")) or cause in (path, word_file) or (
                self.trust.trust(cause)[0] is not None)
            if word_record is None or not cause_ok:
                strict.append(entry)
                continue
            vendor.append({"source": source, "file": word_file, "line": line, "word": entry.get("word"),
                           "word_sha256": entry.get("word_sha256"),
                           "reason": entry.get("detail") or entry["reason"], "variable": entry.get("variable"),
                           "cause_origin": cause, "cause_line": entry.get("origin_line"),
                           "vendor": {key: word_record.get(key) for key in (
                               "package", "nevra", "key", "key_fingerprint", "rpm_verify", "repository",
                               "repository_state", "release")}})
        for item in vendor:
            if item in self.vendor_undecidable:
                continue
            if len(self.vendor_undecidable) >= MAX_VENDOR_UNDECIDABLE:
                self.vendor_undecidable_over_limit += 1
            else:
                self.vendor_undecidable.append(item)
        self.record_vendor(record, source, "scanned", vendor)
        self.workload(source, path, record, text)
        return strict

    def record_vendor(self, record, source, relation, vendor):
        entry = dict(record, source=source, relation=relation, undecidable=len(vendor))
        if entry not in self.vendor_trusted and len(self.vendor_trusted) < MAX_RESOLVED_ENTRIES:
            self.vendor_trusted.append(entry)

    def workload(self, source, path, record, text):
        """Arquivo que o script do fornecedor carrega, roda ou cita em ``/etc``, e ``%config`` alterado do pacote:
        íntegro do fornecedor fica coberto (vai ao recibo); o resto é varrido no estrito como ``workload:``."""
        candidates = {}
        for script in self.scripts.get(source, ()):
            for item in script.loaded:
                candidates.setdefault(_normal_path(item["path"]), "loaded")
            for executed in script.executed_paths():
                candidates.setdefault(executed, "executed")
        for token in TOKEN_RE.findall(text):
            for cited in _token_paths(token):
                cited = _normal_path(cited)
                if cited.startswith("/etc/") and self.fs.is_file(cited):
                    candidates.setdefault(cited, "cited")
        for config in self.trust.modified_config(record["package"]):
            candidates.setdefault(config, "modified_config")
        for candidate, relation in sorted(candidates.items()):
            if candidate == path or (candidate, relation) in self.workload_seen or self.expired():
                continue
            self.workload_seen.add((candidate, relation))
            covered, reason = (None, "file_modified") if relation == "modified_config" else self.trust.trust(candidate)
            if covered is not None:
                self.record_vendor(covered, source, "covered_" + relation, [])
                continue
            try:
                content, cut = self.fs.read(candidate, FILE_LIMIT)
            except (FileNotFoundError, NotRegularFile):
                continue  # ausente ou não é arquivo regular: não roda nem carrega texto
            except OSError:
                self.undecidable_add([{"source": "workload:" + candidate, "reason": "workload_file_unreadable",
                                       "relation": relation, "by": path, "word": candidate}])
                continue
            entry = {"path": candidate, "relation": relation, "by": path, "trust": reason,
                     "text_sha256": hashlib.sha256(content.encode("utf-8", "replace")).hexdigest(), "over_limit": cut}
            if len(self.workload_scanned) < MAX_RESOLVED_ENTRIES:
                self.workload_scanned.append(entry)
            if cut:
                self.undecidable_add([{"source": "workload:" + candidate, "reason": "workload_file_over_limit",
                                       "relation": relation, "by": path, "word": candidate}])
            self.cite(_script_text(content), "workload:" + candidate)

    def record_measured(self, source, script):
        """Glob expandido (o sha256 do texto de cada arquivo regular), ``.`` em ramo morto e ``eval`` decidido, com a
        fonte. Só o arquivo que roda precisa ser lido (``glob_programs``); o resto entra na lista com o que se mediu."""
        entries = []
        for record in script.globs:
            entry = {"source": source, "origin": record["origin"], "line": record["line"],
                     "pattern": record["pattern"], "status": record["status"], "files": []}
            for item in record["files"]:
                try:
                    content, cut = self.fs.read(item["path"], FILE_LIMIT)
                except NotRegularFile:
                    entry["files"].append({"path": item["path"], "text_sha256": None, "regular": False})
                    continue
                except OSError:
                    entry["files"].append({"path": item["path"], "text_sha256": None, "readable": False})
                    continue
                entry["files"].append({"path": item["path"], "text_sha256": hashlib.sha256(
                    content.encode("utf-8", "replace")).hexdigest(), "over_limit": cut})
            if entry not in self.glob_expansions and len(self.glob_expansions) < MAX_RESOLVED_ENTRIES:
                self.glob_expansions.append(entry)
        for record in script.dead_sources:
            entry = dict(record, source=source, test="exists")
            if entry not in self.dead_branches and len(self.dead_branches) < MAX_RESOLVED_ENTRIES:
                self.dead_branches.append(entry)
        for record in script.dead_commands:
            entry = dict(record, source=source)
            if entry not in self.dead_command_branches and len(self.dead_command_branches) < MAX_RESOLVED_ENTRIES:
                self.dead_command_branches.append(entry)
        for record in script.decided_evals:
            texts = record["texts"]
            entry = {"source": source, "origin": record["origin"], "line": record["line"],
                     "alternatives": len(texts), "references": self.receipt_references(texts)}
            if entry not in self.decided_evals and len(self.decided_evals) < MAX_RESOLVED_ENTRIES:
                self.decided_evals.append(entry)
        return entries

    def glob_programs(self, source, script, values):
        """Arquivo do glob que roda na posição de comando: o texto dele é varrido pelo mesmo contrato. Arquivo que não
        se lê (ou passa do limite) deixa a fonte indecidível."""
        expanded = {item["path"]: record for record in script.globs for item in record["files"]}
        entries = []
        for path in sorted(set(values) & set(expanded)):
            try:
                content, cut = self.fs.read(path, FILE_LIMIT)
            except OSError:
                entries.append({"source": source, "reason": "expansion_in_command_position",
                                "detail": "glob_file_unreadable", "variable": None, "origin": path,
                                "line": expanded[path]["line"], "word": path})
                continue
            if cut:
                entries.append({"source": source, "reason": "expansion_in_command_position",
                                "detail": "glob_file_over_limit", "variable": None, "origin": path,
                                "line": expanded[path]["line"], "word": path})
                continue
            if path not in self.glob_seen:
                self.glob_seen.add(path)
                self.cite(_script_text(content), "program:" + path)
        return entries

    def receipt_references(self, values):
        return sorted({reference for value in values for reference in self.value_references(value)})[:MAX_CHECK_ITEMS]

    def record_resolved(self, source, script, item, values):
        variables = []
        raws = ([item["raw"]] if item.get("raw") else []) + [raw for raw in item.get("raws") or [] if raw]
        for raw in raws:
            for match in VAR_REF_RE.finditer(script._restore(raw)):
                expression = match.group(1) or match.group(2) or ""
                array = ARRAY_ALL_RE.match(expression)
                name = array or DEFAULT_OP_RE.match(expression)
                if not name:
                    continue
                try:
                    found, assignments = script.resolve(name.group(1), item["line"], item["function"], 1)
                except _Undecided:
                    found, assignments = set(), []  # decidido só pelo default literal de ``${X:-lit}``
                record = {"name": name.group(1), "values": self.receipt_references(found),
                          "assignments": [dict({"origin": entry.origin, "line": entry.origin_line},
                                               **({"glob": entry.glob} if entry.glob else {}))
                                          for entry in assignments][:MAX_CHECK_ITEMS]}
                if record not in variables:
                    variables.append(record)
        for value in sorted(values):
            for reference in self.value_references(value):
                if {"source": source, "reference": reference} not in self.found:
                    self.found.append({"source": source, "reference": reference})
        entry = {"source": source, "line": item["line"], "values": self.receipt_references(values),
                 "variables": variables}
        if item.get("wrapper"):
            entry["wrapper"] = item["wrapper"]
            entry["grammar"] = [WRAPPER_GRAMMARS[name]["source"] for name in item["wrapper"]]
        if item["kind"] == "none":
            entry["wrapper_mode"] = item["mode"]
        if item["origin"] != script.origin:
            entry["word_origin"], entry["word_line"] = item["origin"], item["origin_line"]
        if entry not in self.resolved and len(self.resolved) < MAX_RESOLVED_ENTRIES:
            self.resolved.append(entry)

    def value_references(self, value):
        """Caminhos da raiz que um valor decidido roda ou cita: o comando (caminho ou nome no PATH) e todo caminho."""
        words = value.split()
        found = set()
        if words and "/" not in words[0] and words[0] in self.names:
            found.add(self.names[words[0]])
        for path in _token_paths(value):
            reference = _usrmerge(_normal_path(path))
            if reference in self.refs:
                found.add(reference)
        return sorted(found)

    def unit_env(self, props):
        """``Environment=`` e ``EnvironmentFile=`` da unidade: ``{nome: {valor}}``; ``None`` quando um arquivo não se lê."""
        env = {}
        for key, _, value in props:
            if key == "Environment" and value.strip():
                try:
                    items = shlex.split(value)
                except ValueError:
                    return None
                for item in items:
                    name, equal, text = item.partition("=")
                    if equal and SHELL_NAME_RE.match(name):
                        env[name] = {text}
            elif key == "EnvironmentFiles" and value.strip():
                path = value.strip().split(" (", 1)[0]
                try:
                    text, cut = self.fs.read(path, FILE_LIMIT)
                except FileNotFoundError:
                    continue  # ausente: o systemd ignora (``-``) ou a unidade não sobe
                except OSError:
                    return None
                if cut:
                    return None
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith(("#", ";")):
                        continue
                    name, equal, text_value = line.partition("=")
                    name = name.strip()
                    if equal and SHELL_NAME_RE.match(name):
                        text_value = text_value.strip()
                        if len(text_value) > 1 and text_value[0] == text_value[-1] and text_value[0] in "\"'":
                            text_value = text_value[1:-1]
                        env[name] = {text_value}
        return env

    def script(self, path):
        """(``#!``, texto citável): o ``#!`` e, se o programa for script, o corpo sem comentário."""
        try:
            head, _ = self.fs.read(path, SHEBANG_LIMIT)
            body, cut = self.fs.read(path, FILE_LIMIT) if head.startswith("#!") else ("", False)
        except FileNotFoundError:
            # Programa que não existe na raiz (unidade de initrd do dracut, plymouth ausente) não cita nada.
            return "", ""
        except OSError:
            self.reasons.add("program_unreadable")
            return "", ""
        if cut:
            self.reasons.add("program_over_limit")
        return self.shebang_text(body)

    @staticmethod
    def shebang_text(body):
        line = body[:SHEBANG_LIMIT].split("\n", 1)[0] if body.startswith("#!") else ""
        # O ``#!`` ocupa a 1ª linha para preservar a numeração do arquivo.
        return line, line + "\n" + _script_text(body).partition("\n")[2] if line else line

    def owner(self, path):
        status = self.owners(path)
        if status in ("failed", "over_limit"):
            self.reasons.add("owner_lookup_failed" if status == "failed" else "owner_queries_over_limit")
        return self.owners.package(path)

    def program(self, path, source, observed=None):
        """Programa executado: arquivo resolvido, dono e o interpretador do ``#!`` com o dono dele."""
        with self.rooted(source):
            return self._program(path, source, observed)

    def _program(self, path, source, observed):
        real = self.paths.final(path)
        interpreter = None
        line, text = self.script(real) if observed is None else observed
        self.cite(text, source)
        self.shebang_imports(path, source, text)
        if line:
            words = line[2:].split()
            interpreter = self.paths.final(words[0]) if words and _clean_path(words[0]) else None
            if interpreter and _usrmerge(interpreter) == "/usr/bin/env":
                candidates = {self.shebang_env_interpreter(words[1:], env)
                              for env in self.shebang_environments(path, real, source)}
                interpreter = candidates.pop() if len(candidates) == 1 else None
                if interpreter is not None:
                    interpreter = self.paths.final(interpreter)
            if interpreter is None:
                self.reasons.add("shebang_unresolved")
        return {"path": path, "resolved_path": real, "owner": self.owner(real), "interpreter": interpreter,
                "interpreter_owner": self.owner(interpreter) if interpreter else None}

    def unit(self, name):
        shown = self.run(["systemctl", "show", "--no-pager", "--property=" + SUCCESSOR_UNIT_PROPERTIES, "--", name])
        if shown["rc"] != 0 or shown["truncated"]:
            self.reasons.add("unit_unreadable")
            return {"unit": name, "read": False}
        props, execs = {}, []
        for line in shown["stdout"].splitlines():
            key, _, value = line.partition("=")
            if key.startswith("Exec") and key != "ExecMainStartTimestampMonotonic":
                execs.append(value)
            else:
                props[key] = value.strip()
        entry = {"unit": name, "read": True, "load_state": props.get("LoadState"),
                 "active_state": props.get("ActiveState"), "result": props.get("Result")}
        if name.endswith(".timer"):
            service = props.get("Unit", "")
            entry["service"] = self.unit(service) if SERVICE_RE.match(service) else None
            if entry["service"] is None:
                self.reasons.add("timer_service_unresolved")
            entry.update(self.next_elapse(name))
            return entry
        started = props.get("ExecMainStartTimestampMonotonic", "")
        entry["ran"] = int(started) > 0 if started.isdigit() else None
        entry["programs"] = []
        commands = self.exec_commands(name)
        if commands is None:
            self.undecidable_add([_undecided_entry("unit:" + name, "exec_structure_unread", name)])
            commands = []
        for path, argv in commands:
            if PATH_RE.match(path):
                entry["programs"].append(self.program(path, "shebang:" + path))
            else:
                self.reasons.add("unsupported_exec_path")
            self.exec_command("unit:" + name, {"path": path, "argv": argv, "env": None})
        self.scanned["unit_exec"].add(name)
        return entry

    def exec_commands(self, unit):
        """``[(caminho, argv)]`` dos Exec* do serviço pelo D-Bus; ``None`` quando a estrutura não se lê inteira."""
        shown = self.run(["busctl", "--system", "--json=short", "get-property", "org.freedesktop.systemd1",
                          "/org/freedesktop/systemd1/unit/" + _bus_label(unit), "org.freedesktop.systemd1.Service",
                          *EXEC_DBUS_PROPERTIES])
        lines = [line for line in shown["stdout"].splitlines() if line.strip()]
        if shown["rc"] != 0 or shown["truncated"] or len(lines) != len(EXEC_DBUS_PROPERTIES):
            return None
        commands = []
        for line in lines:
            try:
                value = json.loads(line)
            except ValueError:
                return None
            data = value.get("data") if isinstance(value, dict) else None
            if not isinstance(data, list):
                return None
            for item in data:
                if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str)
                        and isinstance(item[1], list) and item[1] and all(isinstance(arg, str) for arg in item[1])):
                    return None
                commands.append((item[0], item[1]))
        return commands

    def exec_command(self, source, command):
        """Um Exec*: referência literal no argv, expansão do systemd em quem executa outro comando e o script ou o
        ``-c`` do interpretador (R6-1)."""
        with self.rooted(source):
            self._exec_command(source, command)

    def _exec_command(self, source, command):
        path, argv, env = command["path"], command["argv"], command.get("env")
        name = path.rsplit("/", 1)[-1]
        extra = []
        if _interpreter_named(name) is None and (name in CITE_RUNNERS or name in WRAPPER_GRAMMARS) and name != "env":
            expanded = next((arg for arg in argv[1:] if "$" in arg), None)
            if expanded is not None:  # o systemd troca ``$X`` pelo ambiente da unidade antes de executar
                extra.append(_undecided_entry(source, "systemd_expansion", expanded))
        extra.extend(self.interpreter_target(path, argv, source, env))
        self.cite(" ".join(argv), source, shell=False, extra=extra)

    def interpreter_target(self, path, argv, source, env):
        """Script ou texto de ``-c`` que o interpretador (direto ou depois de ``env``) lê, pela gramática dele: o
        script é varrido pelo mesmo contrato (estrito, resolvedor, regra do fornecedor, referência literal); o
        ``-c`` de shell passa pelo resolvedor. Script ausente ou ilegível, opção desconhecida, código inline de
        outra linguagem ou argumento que a sonda não decide: indecidível."""
        args = list(argv)
        real = self.paths.final(path) if path.startswith("/") else path
        name = real.rsplit("/", 1)[-1]
        if name == "env":
            index = 1
            env = None if env is None else dict(env)
            wrapper_startup_unproven = (env is None or bool(set(PY_STARTUP_ENVIRONMENT) & env.keys())
                                        or real not in ("env", "/bin/env", "/usr/bin/env"))
            while index < len(args):
                arg = args[index]
                if arg in ("-i", "-", "--ignore-environment"):
                    env = {}
                    index += 1
                elif arg == "--":
                    index += 1
                    break
                elif arg in ("-u", "--unset", "-C", "--chdir"):
                    if index + 1 >= len(args):
                        return [_undecided_entry(source, "interpreter_option_unknown", arg)]
                    if env is not None and arg in ("-u", "--unset"):
                        env.pop(args[index + 1], None)
                    index += 2
                elif arg.startswith(("--unset=", "--chdir=")):
                    if env is not None and arg.startswith("--unset="):
                        env.pop(arg.partition("=")[2], None)
                    index += 1
                elif arg.startswith("-"):
                    return [_undecided_entry(source, "interpreter_option_unknown", arg)]
                else:
                    break
            while index < len(args) and SHELL_ASSIGNMENT_RE.match(args[index]):
                key, _, value = args[index].partition("=")
                if env is not None:
                    env[key] = {value}
                index += 1
            if index >= len(args):
                return []
            named = args[index]
            if "$" in named or "`" in named:
                return [_undecided_entry(source, "interpreter_argument_undecided", named)]
            found = self.env_executable(named, env)
            if found is None:
                spec = _interpreter_named(named.rsplit("/", 1)[-1])
                detail = ("python_interpreter_path_unproven" if spec is not None and spec.language == "python"
                          else "env_executable_path_unproven")
                return [_undecided_entry(source, detail, named)]
            path, args = found, args[index:]
            name = self.paths.final(path).rsplit("/", 1)[-1]
            wrapped = _interpreter_named(name)
            if wrapped is not None and wrapped.language == "python" and wrapper_startup_unproven:
                env = None
        spec = _interpreter_named(name)
        if spec is None:
            return []
        scan_args = [arg if "$" not in arg and "`" not in arg else "\x00" for arg in args]
        kind, program, position = _argv_scan(spec, scan_args)
        expanded = next((arg for arg in args[1:position + 1] if "$" in arg or "`" in arg), None)
        if expanded is not None:
            return [_undecided_entry(source, "interpreter_argument_undecided", expanded)]
        if kind == "ambiguous":
            return [_undecided_entry(source, "interpreter_option_unknown", args[position])]
        if kind == "inline":
            option = args[position]
            if spec is SHELL and option.startswith("-") and "c" in option[1:] and position + 1 < len(args):
                text = args[position + 1]
                inline = "inline:" + source
                self.envs.setdefault(inline, []).append(env)
                self.cite(text, inline, shell=True)
                return []
            return [_undecided_entry(source, "interpreter_inline_code", option)]
        if kind == "program":
            return self.interpreter_script(program, source, env, spec is SHELL, path,
                                           _python_flags(args[1:position]))
        return []

    def interpreter_script(self, script, source, env, shell, interp=None, flags=""):
        """Script lido pelo interpretador. Contrato do recibo: ``interpreter_script_unreadable``,
        ``interpreter_script_relative`` e ``interpreter_script_over_limit`` gravam o caminho do script (é a identidade
        do arquivo, não texto de comando). O texto passa pelo mesmo contrato e os imports dele por ``script_imports``."""
        caller = self.source_file(source) or source
        if not script.startswith("/"):
            return [_undecided_entry(source, "interpreter_script_relative", script, origin=caller)]
        if script not in self.interpreter_scripts:
            if len(self.interpreter_scripts) >= MAX_RESOLVED_ENTRIES or self.expired():
                return [_undecided_entry(source, "interpreter_script_over_limit", script, origin=caller)]
            try:
                text, cut = self.fs.read(script, FILE_LIMIT)
            except OSError:
                text, detail = None, "interpreter_script_unreadable"
            else:
                detail = "interpreter_script_over_limit" if cut else None
            self.interpreter_scripts[script] = (None if detail else text, detail)
        text, detail = self.interpreter_scripts[script]
        if detail:
            return [_undecided_entry(source, detail, script, origin=caller)]
        script_source = "script:" + script
        self.mark_root(script_source)
        if (script, shell) not in self.interpreter_seen:
            self.interpreter_seen.add((script, shell))
            self.cite(_script_text(text), script_source, shell=False)
        if shell:
            self.interpreter_shell(script, text, script_source, env)
        elif interp is not None:
            self.interpreter_imports(interp, flags, script, text, script_source, env)
        return []

    def interpreter_shell(self, script, text, source, env):
        root = self.via_stack[0] if self.via_stack else source
        environment = None if env is None else tuple(sorted((name, frozenset(values)) for name, values in env.items()))
        key = ("shell", script, root, environment)
        if self.admit_interpreter_context(key, source, script, "shell"):
            text = _script_text(text)
            with self.rooted(source):
                entries = self.resolve_command_words(text, source, [env])
                self.undecided(text, source, shell=False, extra=entries)

    def admit_interpreter_context(self, key, source, script, interp):
        if key in self.interpreter_contexts:
            return False
        if len(self.interpreter_contexts) >= MAX_RESOLVED_ENTRIES or self.expired():
            self.undecided("", source, shell=False, extra=[
                _undecided_entry(source, "interpreter_context_over_limit", interp, origin=script)])
            return False
        self.interpreter_contexts.add(key)
        return True

    def interpreter_imports(self, interp, flags, script, text, source, env):
        environment = self.python_environment(flags, env) if flags is not None else None
        # Uma mudança de entrada ou de startup exige reavaliar imports, mesmo com texto já lido.
        # Valores do ambiente não distinguem contextos que a sonda já recusa.
        root = self.via_stack[0] if self.via_stack else source
        key = (script, interp, flags, root, None if environment is None else tuple(sorted(environment)))
        if not self.admit_interpreter_context(key, source, script, interp):
            return
        entries = self.script_imports(interp, flags, script, text, source, [env])
        if entries:
            self.undecided("", source, shell=False, extra=entries)

    def root_tokens(self):
        return set(self.names) | set(self.refs)

    def redact_entry(self, entry, keep):
        word = entry.get("word")
        if not isinstance(word, str) or "word_sha256" in entry:
            return entry
        provenance = json.dumps([entry.get(key) for key in (
            "source", "reason", "detail", "origin", "line", "origin_line", "word_origin", "word_line",
            "variable", "wrapper", "file", "cause_origin", "cause_line",
        )], ensure_ascii=True)
        occurrences = self.word_occurrences.setdefault(provenance, {})
        # O literal distingue ocorrências só em memória. O identificador do recibo usa apenas proveniência e ordinal.
        ordinal = occurrences.setdefault(word, len(occurrences))
        identity = "%s:%d" % (provenance, ordinal)
        entry["word_sha256"] = hashlib.sha256(identity.encode("ascii")).hexdigest()
        entry["word"] = _redact_word(word, keep) or REDACTED
        return entry

    def undecidable_add(self, entries):
        keep = self.root_tokens()
        for entry in entries:
            self.redact_entry(entry, keep)
            if entry in self.undecidable:
                continue
            if len(self.undecidable) >= MAX_UNRESOLVED_ENTRIES:
                self.undecidable_over_limit += 1
            else:
                self.undecidable.append(entry)

    def next_elapse(self, name):
        """Próximo disparo do timer em epoch e o relógio de B na leitura, para a N5 pedir nova coleta depois dele.

        Os dois valores brutos vêm do D-Bus do systemd (``busctl get-property``, µs): ``NextElapseUSecRealtime`` (timer
        de calendário) ou ``NextElapseUSecMonotonic`` (``OnBootSec``/``OnUnitInactiveSec``, relativo ao relógio
        monotônico, o mesmo de ``time.monotonic()`` neste processo). ``systemctl show`` deixa o primeiro vazio no timer
        monotônico e formata o segundo como intervalo. Sem leitura, o epoch fica ``None``.
        """
        observed, monotonic_now = time.time(), time.monotonic()
        entry = {"next_elapse": None, "next_elapse_epoch": None, "observed_epoch": int(observed)}
        shown = self.run(["busctl", "--system", "get-property", "org.freedesktop.systemd1",
                          "/org/freedesktop/systemd1/unit/" + _bus_label(name), "org.freedesktop.systemd1.Timer",
                          "NextElapseUSecRealtime", "NextElapseUSecMonotonic"])
        values = [line.split() for line in shown["stdout"].splitlines() if line.strip()]
        if shown["rc"] != 0 or shown["truncated"] or len(values) != 2 or not all(
                len(value) == 2 and value[0] == "t" and value[1].isdigit() for value in values):
            return entry
        # USEC_INFINITY (2^64-1) é "sem próximo disparo" nesse relógio, como o 0.
        realtime, monotonic = (int(value[1]) if int(value[1]) < USEC_INFINITY else 0 for value in values)
        if realtime > 0:
            entry.update(next_elapse="realtime", next_elapse_epoch=realtime // 1000000)
        elif monotonic > 0:
            entry.update(next_elapse="monotonic",
                         next_elapse_epoch=int(observed + monotonic / 1000000.0 - monotonic_now))
        return entry

    def _requirer_files(self, name):
        """Executáveis, bibliotecas e módulos Python do requerente, pela lista inteira do ``rpm -ql`` lida linha a
        linha (a lista do ``python3-pip`` passa de 100 KB, acima do teto de ``run``). ``None`` quando não se lê
        inteira."""
        files, state = {"executables": [], "libraries": [], "python_modules": 0}, {"bad": False}

        def on_line(line):
            path = line.strip()
            if not path or path == "(contains no files)":
                return
            if not path.startswith("/"):
                state["bad"] = True
                return
            merged = _usrmerge(path)
            if merged.startswith(EXECUTABLE_PREFIXES):
                files["executables"].append(path)
            elif LIBRARY_RE.match(merged):
                files["libraries"].append(path)
            elif PYTHON_LIBRARY_RE.match(merged):
                files["python_modules"] += 1

        receipt = self.stream(["rpm", "-ql", "--", name], on_line)
        if receipt.get("rc") != 0 or receipt.get("truncated") or state["bad"]:
            return None
        return files

    def requirer(self, name):
        """Executáveis do requerente em B, com o ``#!`` lido, e as bibliotecas ELF com cada DT_NEEDED resolvido.
        ``complete`` só com a lista inteira lida; ``python_modules`` conta os módulos que a sonda não mede."""
        entry = {"package": name, "installed": _installed(name, self.run), "executables": [], "libraries": [],
                 "python_modules": 0, "complete": False}
        listed = self._requirer_files(name) if entry["installed"] else None
        if entry["installed"] is None or (entry["installed"] and listed is None):
            self.reasons.add("requirer_files_unavailable")
        files = listed["executables"] if listed is not None else None
        for path in files or ():
            try:
                mode = self.fs.mode(path)
            except FileNotFoundError:
                continue
            except OSError:
                self.reasons.add("requirer_file_unreadable")
                return entry
            if not stat.S_ISREG(mode) or not mode & 0o111:
                continue
            if len(entry["executables"]) >= MAX_REQUIRER_EXECUTABLES:
                self.reasons.add("requirer_executables_over_limit")
                return entry
            if self.expired():
                return entry  # lista não terminada: ``complete`` fica falso e ``time_budget`` vai a ``reasons``
            entry["executables"].append(self.program(path, f"requirer:{name}:{path}"))
        if listed is None:
            return entry
        entry["python_modules"] = listed["python_modules"]
        if not self.requirer_libraries(entry, listed["libraries"]):
            return entry
        entry["complete"] = True
        self.scanned["requirer_executables"].add(name)
        if entry["libraries"]:
            self.scanned["requirer_libraries"].add(name)
        return entry

    def requirer_libraries(self, entry, paths):
        """Biblioteca ELF do requerente (o arquivo, uma vez por ``realpath``): cada DT_NEEDED com o arquivo que o
        carregador abriria em B e o dono dele. ``False`` quando a lista não termina (ilegível, teto ou tempo)."""
        seen = set()
        for path in paths:
            try:
                mode = self.fs.mode(path)
            except FileNotFoundError:
                continue
            except OSError:
                self.reasons.add("requirer_library_unreadable")
                return False
            real = self.paths.final(path)
            if not stat.S_ISREG(mode) or real in seen:
                continue
            seen.add(real)
            if len(entry["libraries"]) >= MAX_REQUIRER_LIBRARIES:
                self.reasons.add("requirer_libraries_over_limit")
                return False
            if self.expired():
                return False
            try:
                data, cut = self.fs.read_bytes(real, LIBRARY_READ_LIMIT)
                elf = _elf_dynamic(data)
            except (OSError, ValueError):
                self.reasons.add("requirer_library_unreadable")
                return False
            if elf is None:
                continue  # não é ELF (script de ligação do ``ld``): o carregador não abre
            if cut:
                self.reasons.add("requirer_library_over_size")
                return False
            entry["libraries"].append({
                "path": path, "resolved_path": real, "owner": self.owner(real),
                "needed": [self.needed(soname, real, elf) for soname in elf["needed"]]})
        return True

    def needed(self, soname, library, elf):
        """Arquivo que o ``ld.so`` abriria para o soname, da mesma classe e máquina, e o dono dele; ``path`` nulo
        quando nenhuma pasta tem o soname, ``reason`` quando a busca não se decide."""
        found = {"soname": soname, "path": None, "owner": None}
        origin = library.rsplit("/", 1)[0] or "/"
        search = [part.replace("${ORIGIN}", origin).replace("$ORIGIN", origin) for part in elf["search"]]
        if "/" in soname or any("$" in part or not part.startswith("/") for part in search):
            found["reason"] = "loader_search_unresolved"
            return found
        conf = self.loader_dirs()
        if conf is None:
            found["reason"] = "ld_so_conf_unread"
            return found
        for directory in search + conf + list(LOADER_DEFAULT_DIRS.get(elf["class"], ())):
            candidate = directory.rstrip("/") + "/" + soname
            if not self.fs.is_file(candidate):
                continue
            real = self.paths.final(candidate)
            try:
                identity = _elf_identity(self.fs.read_bytes(real, 20)[0])
            except OSError:
                identity = None
            if identity != (elf["class"], elf["machine"]):
                continue  # o ``ld.so`` pula arquivo de outra classe ou máquina
            found["path"], found["owner"] = real, self.owner(real)
            return found
        return found

    def loader_dirs(self):
        """Pastas de ``/etc/ld.so.conf`` com ``include`` (glob relativo a ``/etc``), na ordem; ``None`` quando a
        configuração não se lê inteira."""
        if self.loader_conf_read:
            return self.loader_conf
        self.loader_conf_read, self.loader_conf = True, self._loader_conf()
        return self.loader_conf

    def _loader_conf(self):
        dirs, pending, read = [], [LD_SO_CONF], 0
        while pending:
            path = pending.pop(0)
            read += 1
            if read > MAX_LD_SO_CONF_FILES:
                return None
            try:
                text, cut = self.fs.read(path, FILE_LIMIT)
            except FileNotFoundError:
                if path == LD_SO_CONF:
                    continue
                return None
            except OSError:
                return None
            if cut:
                return None
            for line in text.splitlines():
                words = line.split("#", 1)[0].replace(",", " ").replace(":", " ").split()
                if not words or words[0] == "hwcap":
                    continue
                if words[0] == "include":
                    for pattern in words[1:]:
                        pattern = pattern if pattern.startswith("/") else "/etc/" + pattern
                        directory, glob = pattern.rsplit("/", 1)
                        try:
                            names = self.fs.listdir(directory)
                        except FileNotFoundError:
                            continue
                        except OSError:
                            return None
                        pending.extend(directory + "/" + name for name in sorted(names)
                                       if fnmatch.fnmatchcase(name, glob))
                    continue
                dirs.extend(word for word in words if word.startswith("/") and word not in dirs)
        return dirs

    def crontabs(self):
        """Texto dos crontabs do sistema e dos scripts de ``/etc/cron.*``, lido uma vez, sem comentário."""
        if self.cron is None:
            paths = list(CRON_FILES)
            for directory in SYSTEM_CRON_DIRS:
                try:
                    names = self.fs.listdir(directory)
                except FileNotFoundError:
                    continue
                except OSError:
                    self.reasons.add("crontab_unreadable")
                    continue
                if len(names) > MAX_SCHEDULE_FILES:
                    self.reasons.add("crontab_files_over_limit")
                paths.extend(f"{directory}/{name}" for name in names[:MAX_SCHEDULE_FILES])
            self.cron = []
            for path in paths:
                try:
                    text, cut = self.fs.read(path, FILE_LIMIT)
                except (FileNotFoundError, NotRegularFile):
                    continue
                except OSError:
                    self.reasons.add("crontab_unreadable")
                    continue
                if cut:
                    self.reasons.add("crontab_over_limit")
                self.cron.append((path, _script_text(text)))
                self.scanned["system_crontabs"].add(path)
            for directory, key in ((CRON_TABLE_DIRS[1], "user_crontab_spool"), (AT_SPOOL, "at_jobs")):
                self.spool(directory, key)
        return self.cron

    def spool(self, directory, key):
        """Crontab de usuário ou job de ``at``. Sem permissão de listar, a fonte fica em ``limit``; diretório ausente
        é fonte lida e vazia. Arquivo que não abre deixa a busca ``partial``."""
        self.scanned.setdefault(key, set())
        try:
            names = self.fs.listdir(directory)
        except FileNotFoundError:
            return
        except PermissionError:
            self.unread.add(key)
            return
        except OSError:
            self.reasons.add("crontab_unreadable")
            return
        names = [name for name in names if not name.startswith(".")]
        if len(names) > MAX_SCHEDULE_FILES:
            self.reasons.add("crontab_files_over_limit")
        for name in names[:MAX_SCHEDULE_FILES]:
            path = "%s/%s" % (directory, name)
            try:
                text, cut = self.fs.read(path, FILE_LIMIT)
            except (FileNotFoundError, NotRegularFile):
                continue
            except OSError:
                self.reasons.add("crontab_unreadable")
                continue
            if cut:
                self.reasons.add("crontab_over_limit")
            self.cron.append((path, _script_text(text)))
            self.scanned[key].add(path)

    def outside(self):
        """Exec* de toda unidade de serviço carregada (as pedidas incluídas) e o texto dos programas dela, lidos uma
        vez. Sem a lista inteira, a fonte fica em ``limit`` (``programs_outside_requested_units``)."""
        if self.others is not None:
            return self.others
        self.others = []
        listed = self.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain",
                           "--no-pager"])
        names = [line.split()[0] for line in listed["stdout"].splitlines() if line.split()]
        if listed["rc"] != 0 or listed["truncated"] or len(names) > MAX_OUTSIDE_UNITS or not all(
                SERVICE_RE.match(name) for name in names):
            self.unread.add("programs_outside_requested_units")
            return self.others
        for start in range(0, len(names), OUTSIDE_BATCH):
            if self.expired():
                return self.others
            shown = self.run(["systemctl", "show", "--no-pager", "--property=Id," + OUTSIDE_EXEC_PROPERTIES, "--",
                              *names[start:start + OUTSIDE_BATCH]])
            if shown["rc"] != 0 or shown["truncated"]:
                self.unread.add("programs_outside_requested_units")
                return self.others
            # Um bloco por unidade, separado por linha vazia; o systemd não imprime ``Id`` antes dos Exec*.
            for block in shown["stdout"].split("\n\n"):
                if not block.strip():
                    continue
                props = [line.partition("=") for line in block.splitlines()]
                unit = next((value.strip() for key, _, value in props if key == "Id"), "")
                self.scanned["loaded_service_exec"].add(unit)
                env = self.unit_env(props)
                commands = self.exec_commands(unit) if SERVICE_RE.match(unit) else None
                if commands is None:
                    self.others.append(("unit:" + unit, {"unread": unit}))
                    continue
                for path, argv in commands:
                    self.others.append(("unit:" + unit, {"path": path, "argv": argv, "env": env}))
                    if PATH_RE.match(path):
                        self.envs.setdefault("program:" + path, []).append(env)
                        self.others.append(("program:" + path, self.script(self.paths.final(path))[1]))
        return self.others

    def check(self, check):
        self.refs, self.found = frozenset(_usrmerge(_normal_path(path)) for path in check.get("reference_paths", [])), []
        self.undecidable, self.undecidable_over_limit, self.resolved = [], 0, []
        self.word_occurrences = {}
        self.glob_expansions, self.dead_branches, self.decided_evals, self.glob_seen = [], [], [], set()
        self.vendor_undecidable, self.vendor_undecidable_over_limit = [], 0
        self.vendor_trusted, self.trust_refused, self.workload_scanned = [], [], []
        self.workload_seen, self.scripts = set(), {}
        self.interpreter_scripts, self.interpreter_seen, self.interpreter_contexts = {}, set(), set()
        # Comando da raiz num diretório do PATH também é chamado só pelo nome.
        self.names = {ref.rsplit("/", 1)[1]: ref for ref in self.refs if ref.rsplit("/", 1)[0] in STANDARD_BIN_DIRS}
        self.python_cited, self.python_receipt, self.python_scanned = set(), [], 0
        self.python_deadline = None
        self.python_limits = {"module_cap": PY_IMPORT_MAX_MODULES, "depth_cap": PY_IMPORT_MAX_DEPTH,
                              "time_budget_s": PY_IMPORT_TIME_BUDGET_S, "over_limit": False, "time_budget_hit": False}
        self.loaded_cited = set()
        self.via_stack, self.roots, self.exec_seen, self.exec_count = [], {}, set(), 0
        self.executed_binaries = []
        self.touched, self.exec_children, self.exec_cache = [], {}, {}  # fontes tocadas, por programa (R10-1)
        self.dead_command_branches = []
        self.outside()  # o ambiente das unidades (EnvironmentFile) vale para o programa das unidades pedidas também
        successor = check.get("successor")
        result = {"package": check["package"], "successor": successor, "path_owner": check.get("path_owner") is True,
                  "root_installed": _installed(check["package"], self.run),
                  "successor_installed": _installed(successor, self.run) if successor else None,
                  "units": [], "requirers": []}
        for name in check.get("units", []):
            if not self.expired():
                result["units"].append(self.unit(name))
        for name in check.get("requirers", []):
            if not self.expired():
                result["requirers"].append(self.requirer(name))
        for path, text in self.crontabs() + self.outside():
            if isinstance(text, dict) and "unread" in text:
                self.undecidable_add([_undecided_entry(path, "exec_structure_unread", text["unread"])])
            elif isinstance(text, dict):
                self.exec_command(path, text)
            else:
                with self.rooted(path):
                    self.cite(text, path)
                    if path.startswith("program:"):
                        self.shebang_imports(path.split(":", 1)[1], path, text)
        self.root_origins()
        result["references"] = self.found
        result["undecidable_references"] = self.undecidable
        result["undecidable_over_limit"] = self.undecidable_over_limit
        result["resolved_command_variables"] = self.resolved
        result["glob_expansions"] = self.glob_expansions
        result["dead_source_branches"] = self.dead_branches
        result["dead_command_branches"] = self.dead_command_branches
        result["decided_evals"] = self.decided_evals
        result["vendor_code_undecidable"] = self.vendor_undecidable
        result["vendor_code_undecidable_over_limit"] = self.vendor_undecidable_over_limit
        result["vendor_trusted"] = self.vendor_trusted
        result["vendor_trust_refused"] = self.trust_refused
        result["workload_scanned"] = self.workload_scanned
        result["vendor_release"] = self.trust._release
        result["python_imports"] = dict(self.python_limits, contexts=self.python_receipt,
                                        modules_read=self.python_scanned)
        result["executed_programs"] = {"read": self.exec_count, "binaries": self.executed_binaries}
        result["reference_owners"] = [self.reference_owner(path) for path in sorted(self.refs)]
        return result

    def root_origins(self):
        """Origem raiz de cada item: o requerente ou o sucessor que levou à leitura (módulo importado, script,
        programa). Só sai quando difere da própria fonte; a N5 lê ``via`` ou, sem ele, a fonte."""
        for entry in self.found + self.undecidable + self.vendor_undecidable:
            roots = self.roots.get(entry["source"], {entry["source"]})
            if roots != {entry["source"]}:
                entry["via"] = sorted(roots)

    def reference_owner(self, path):
        """Dono em B do caminho de referência da raiz e do arquivo que ele resolve (``realpath``): a N5 só ignora a
        referência quando os dois existem e são de outro pacote instalado. Caminho ausente ou consulta sem resposta
        fica ``null`` (a referência conta)."""
        try:
            self.fs.mode(path)
        except OSError:
            return {"path": path, "exists": False, "owner": None, "resolved_path": None, "resolved_owner": None}
        resolved = self.paths.final(path)
        return {"path": path, "exists": True, "owner": self.owners.package(path), "resolved_path": resolved,
                "resolved_owner": self.owners.package(resolved)}


def _scriptlets(stream):
    """Scriptlets de todo pacote instalado: que pacotes podem escrever em cada spool. ``complete`` só com a
    consulta inteira lida (rc 0, sem corte); fora disso, a fonte fica em ``coverage.limit``."""
    state = {"package": None, "packages": 0, "writers": collections.defaultdict(set)}

    def on_line(line):
        if line.startswith(SCRIPTLET_MARK):
            state["package"] = line[len(SCRIPTLET_MARK):].strip()
            state["packages"] += 1
            return
        for key in _scriptlet_spools(line):
            state["writers"][key].add(state["package"] or "")

    receipt = stream(SCRIPTLET_QUERY, on_line)
    complete = receipt.get("rc") == 0 and not receipt.get("truncated") and state["packages"] > 0
    return {"complete": complete, "packages": state["packages"],
            "spool_writers": {key: sorted(state["writers"].get(key, ())) for key in ("at_jobs", "user_crontab_spool")}}


def _successor_runtime(checks, run, fs, stream=None, history=None):
    started = time.monotonic()
    scan = _SuccessorScan(run, fs, started + SUCCESSOR_TIME_BUDGET_S, stream)
    results = [scan.check(check) for check in checks]
    scriptlets = _scriptlets(scan.stream) if checks else None
    if scriptlets is None or not scriptlets["complete"]:
        scan.unread.add("package_scriptlets")
    package_history = (history or read_package_history)() if checks else None
    if package_history is None or not package_history["complete"]:
        scan.unread.add("package_history")
    reasons = sorted(scan.reasons | (set() if checks else {"request_invalid"}))
    return {"checks": results, "elapsed_ms": _elapsed_ms(started),
            "coverage": {"status": "partial" if reasons else "complete", "reasons": reasons,
                         "scanned": {key: sorted(value) for key, value in sorted(scan.scanned.items())},
                         "limit": sorted(scan.unread) if checks else list(SUCCESSOR_SOURCES),
                         "reference_forms": ["absolute_path", "path_name"], "scriptlets": scriptlets,
                         "package_history": package_history}}


def probe_sha256(file_name=None, source=None):
    """sha256 dos bytes desta sonda, lidos em tempo de execução: identidade da versão que mediu.

    Roda por caminho (``file_name``) ou pelo texto que o playbook entrega no stdin (``source``, ``PROBE_TEXT`` do
    lançador, o mesmo ``lookup('file')`` do arquivo). ``None`` sem nenhum dos dois: o recibo sai sem identidade e
    não é reaproveitado.
    """
    if source is None:
        try:
            with open(file_name, "rb") as handle:
                source = handle.read().decode("utf-8")
        except (OSError, TypeError, UnicodeDecodeError):
            return None
    # O mesmo ``str.rstrip()`` do ``lookup('file')`` sobre o texto UTF-8 (cobre espaço Unicode): arquivo e texto
    # entregue dão a mesma identidade.
    return hashlib.sha256(source.rstrip().encode("utf-8")).hexdigest()


def collect(request, run=command, fs=None, stream=None):
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
        # Identidade da versão que mediu: o REE só reaproveita recibo cuja sonda é a que o produto lançaria.
        "probe_sha256": probe_sha256(file_name=globals().get("__file__"), source=globals().get("PROBE_TEXT")),
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
    checks = _successor_request(request)
    if checks is not None:
        observation["successor_runtime"] = _successor_runtime(checks, run, fs, stream or getattr(run, "stream", None),
                                                               getattr(run, "history", None))
    return observation


if __name__ == "__main__":
    # O request chega em argv[1] ou no stdin; o lançador do playbook põe o request no stdin antes de executar a sonda.
    request = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    print(json.dumps(collect(request), sort_keys=True, separators=(",", ":")))
