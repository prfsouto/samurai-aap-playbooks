#!/usr/bin/env bash
# ============================================================================
# SamurAI Shield :: apt security-sources discovery (#3037 / PR #3083)
# ----------------------------------------------------------------------------
# Materializa a visão SECURITY-ONLY das origens apt do host, nos DOIS formatos
# que o apt aceita:
#
#   * formato clássico de uma linha  — /etc/apt/sources.list e
#     /etc/apt/sources.list.d/*.list  (linhas `deb`/`deb-src` cuja suite é
#     *-security);
#   * formato Deb822                 — /etc/apt/sources.list.d/*.sources
#     (stanzas cujo campo `Suites:` contém uma suite *-security). A stanza é
#     copiada INTEIRA: URIs, Suites, Components e opções (Signed-By,
#     Architectures, ...) são preservados — recortar campos quebraria a
#     validade do source.
#
# Saída (sempre criada, mesmo vazia — o apt exige que os caminhos existam):
#   $OUT/security_only_sources.list          linhas clássicas selecionadas
#   $OUT/security_only.sources.d/            dir p/ Dir::Etc::SourceParts
#   $OUT/security_only.sources.d/samurai-security.sources   stanzas Deb822
#
# stdout: "classic=<n> deb822=<n>" — o playbook decide a RECUSA (nenhuma
# origem de segurança => nunca alargar para full upgrade); este script não
# decide política, só descobre. Sai 0 mesmo sem origem (a decisão é do play).
#
# Uso: samurai_apt_security_sources.sh <output_dir> [apt_root]
#   apt_root (default /etc/apt) existe para os testes de unidade apontarem
#   para fixtures — o runtime nunca passa o 2º argumento.
# ============================================================================
set -euo pipefail

OUT="${1:?usage: samurai_apt_security_sources.sh <output_dir> [apt_root]}"
APT_ROOT="${2:-/etc/apt}"

CLASSIC_OUT="$OUT/security_only_sources.list"
DEB822_DIR="$OUT/security_only.sources.d"
DEB822_OUT="$DEB822_DIR/samurai-security.sources"

mkdir -p "$DEB822_DIR"
: > "$CLASSIC_OUT"
: > "$DEB822_OUT"

# ── formato clássico ─────────────────────────────────────────────────────────
# Seleciona linhas deb/deb-src cuja SUITE (token pós-URI, pulando o bloco
# opcional [opções]) termina em -security. Grep amplo em '-security' pegaria
# um URI com '-security' no hostname; o awk mira o campo certo.
{
  cat "$APT_ROOT/sources.list" 2>/dev/null || true
  cat "$APT_ROOT"/sources.list.d/*.list 2>/dev/null || true
} | awk '
  /^[[:space:]]*deb(-src)?[[:space:]]/ {
    i = 2
    if ($i ~ /^\[/) { while (i <= NF && $i !~ /\]$/) i++; i++ }  # pula [opts]
    i++                                                          # pula o URI
    if (i <= NF && $i ~ /-security$/) print $0
  }
' >> "$CLASSIC_OUT"

# ── formato Deb822 ───────────────────────────────────────────────────────────
# Paragraph mode (RS=""): cada stanza é um registro; seleciona as que têm
# alguma suite *-security no campo Suites: e as copia inteiras.
for f in "$APT_ROOT"/sources.list.d/*.sources; do
  [ -e "$f" ] || continue
  awk '
    BEGIN { RS = ""; FS = "\n" }
    {
      sel = 0
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[Ss]uites:/) {
          n = split($i, parts, /[[:space:]]+/)
          for (j = 2; j <= n; j++) if (parts[j] ~ /-security$/) sel = 1
        }
      }
      if (sel) print $0 "\n"
    }
  ' "$f" >> "$DEB822_OUT"
done

classic_count=$(grep -c -E '^[[:space:]]*deb' "$CLASSIC_OUT" 2>/dev/null || true)
deb822_count=$(grep -c -E '^[Uu][Rr][Ii][Ss]?:' "$DEB822_OUT" 2>/dev/null || true)
echo "classic=${classic_count:-0} deb822=${deb822_count:-0}"
