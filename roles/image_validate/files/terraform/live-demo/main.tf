# ============================================================================
# Candidate B — the throwaway machine of an image validation / Live Demo
# (RFC-004 §17.1, N5A). Invoked by the `image_validate` role in provision mode.
#
# Deliberately NOT the two other Terraform trees in this repo:
#   * `packer/terraform`      records a finished build (terraform_data); it
#                             creates nothing and must never create anything.
#   * `ansible/terraform`     is the reference module for a REBUILD, which
#                             replaces a customer server and resolves its image
#                             by name.
#
# This one creates a machine that exists to be looked at and then thrown away.
# Three consequences shape it:
#
# 1. The AMI arrives as an ID, never as a name filter. The plan already froze
#    exactly which image is being demonstrated; resolving `most_recent` by name
#    would let a newer image answer a question that was asked about an older
#    one — and the answer would look correct.
# 2. Nothing here is reachable from the internet. No public address is
#    requested and no ingress is opened; the machine is reached over the
#    network it is placed in. Exposure is a decision, not a side effect.
# 3. No `create_before_destroy`. The whole point of this instance is that it
#    ends cleanly, so nothing may outlive a destroy.
# ============================================================================

terraform {
  # 1.10 is the floor because the backend below relies on `use_lockfile` for
  # native S3 state locking, which does not exist before it. On an older binary
  # the argument is silently ignored and two concurrent runs can corrupt the
  # state — the failure mode is data loss, not an error message.
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Backend is intentionally left partial: the caller supplies the values via
  # `-backend-config` at init time, because the state key belongs to ONE
  # execution. The state must outlive the apply — a destroy that cannot find its
  # state cannot prove that anything was destroyed.
  #
  # The caller MUST pass all of:
  #   bucket        — durable state bucket
  #   key           — image-validate/org=<org>/exec=<exec>/terraform.tfstate
  #   region        — the BUCKET's region, which is not the AMI's region
  #   encrypt=true
  #   kms_key_id    — explicit customer-managed key
  #   use_lockfile=true
  #
  # `encrypt=true` alone yields SSE-S3. This state carries tenant identity,
  # execution identity and image lineage, and the project's posture is explicit
  # KMS-managed encryption for execution data rather than trusting a bucket
  # default — the same rule that governs writes to the server-facts bucket.
  backend "s3" {}
}

variable "ami_id" {
  description = "Exact AMI the demo machine boots from (frozen in the approved plan)."
  type        = string

  validation {
    condition     = can(regex("^ami-[0-9a-f]{8,}$", var.ami_id))
    error_message = "ami_id must be a concrete AMI id (ami-...), never a name or filter."
  }
}

variable "region" {
  description = "AWS region the AMI lives in and the machine is created in."
  type        = string
}

variable "instance_type" {
  description = "Governed instance type for the throwaway machine."
  type        = string
  default     = "t3.small"
}

variable "subnet_id" {
  description = "Governed subnet. Empty resolves it from the governed parameters."
  type        = string
  default     = ""
}

variable "security_group_id" {
  description = "Governed security group. Empty resolves it from the governed parameters."
  type        = string
  default     = ""
}

variable "network_parameter_prefix" {
  description = "SSM prefix where the infrastructure publishes the validation network."
  type        = string
  default     = "/samurai-shield/image-validate/network"
}

variable "assign_public_address" {
  description = "Routable address for the collector. Empty resolves it from the governed parameters."
  type        = string
  default     = ""
}

variable "organization_id" {
  description = "Owning tenant — half of the identity every teardown filters on."
  type        = string
}

variable "execution_id" {
  description = "The one execution this machine belongs to — the other half."
  type        = string
}

variable "image_version_id" {
  description = "ImageVersion being demonstrated, for lineage on the instance."
  type        = string
}

variable "candidate_bootstrap" {
  description = <<-EOT
    Dialeto de bootstrap DECLARADO pela policy da plataforma: como a candidata
    autoriza a chave do coletor no primeiro boot. Este módulo não infere a
    família — nem pelo nome da conta, nem pela AMI. Quem sabe é o documento
    governado, e o valor chega daqui de cima.
  EOT
  type        = string
  default     = "posix_shell"

  validation {
    condition     = contains(["posix_shell", "windows_powershell"], var.candidate_bootstrap)
    error_message = "candidate_bootstrap must be posix_shell or windows_powershell."
  }
}

variable "ssh_username" {
  description = "Account used by the governed inventory collector."
  type        = string
  default     = "ubuntu"

  validation {
    # A forma da conta muda com a plataforma (``ec2-user`` × ``Administrator``);
    # o formato POSIX estrito continua exigido, mas na precondition do recurso,
    # onde o dialeto declarado pode ser lido junto. Aqui fica só o que vale para
    # qualquer família: nome de conta, nunca caminho nem injeção de shell.
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9._-]*$", var.ssh_username))
    error_message = "ssh_username must be a valid account name."
  }
}

variable "ssh_public_key" {
  description = "Public key for the source host credential, used only at boot."
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition = trimspace(var.ssh_public_key) == "" || can(regex(
      "^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+$",
      trimspace(var.ssh_public_key),
    ))
    error_message = "ssh_public_key must be a supported OpenSSH public key."
  }
}

variable "name_prefix" {
  description = "Recognisable name prefix in the cloud account."
  type        = string
  default     = "samurai-image-validate"
}

provider "aws" {
  region = var.region
}

# ── Network resolution ─────────────────────────────────────────────────────
# Letting the account decide is what broke plan 49: an empty variable became a
# null attribute, and RunInstances answered `VPCIdNotSpecified` because this
# account has no default VPC. There is no safe fallback here — a throwaway
# machine that boots from an unvalidated image must land on the network someone
# chose for it, so an unresolvable network fails before anything is created.
#
# The values live with the infrastructure that owns them, not with the caller:
# these data sources fail the plan when the parameters are absent.
data "aws_ssm_parameter" "subnet_id" {
  count = var.subnet_id == "" ? 1 : 0
  name  = "${var.network_parameter_prefix}/subnet-id"
}

data "aws_ssm_parameter" "security_group_id" {
  count = var.security_group_id == "" ? 1 : 0
  name  = "${var.network_parameter_prefix}/security-group-id"
}

data "aws_ssm_parameter" "assign_public_address" {
  count = var.assign_public_address == "" ? 1 : 0
  name  = "${var.network_parameter_prefix}/assign-public-address"
}

locals {
  subnet_id = var.subnet_id != "" ? var.subnet_id : nonsensitive(data.aws_ssm_parameter.subnet_id[0].value)
  security_group_id = (
    var.security_group_id != ""
    ? var.security_group_id
    : nonsensitive(data.aws_ssm_parameter.security_group_id[0].value)
  )
  assign_public_address = tobool(
    var.assign_public_address != ""
    ? var.assign_public_address
    : nonsensitive(data.aws_ssm_parameter.assign_public_address[0].value)
  )
  ssh_public_key = trimspace(var.ssh_public_key)

  # Os dois dialetos fazem a MESMA coisa — autorizar a chave do coletor para a
  # conta declarada, sem criar conta nenhuma e sem tocar em senha. O que muda é
  # a língua da plataforma. Qual deles vale é decisão da policy, não deste
  # módulo.
  candidate_user_data_posix = <<-EOT
#cloud-boothook
#!/bin/sh
set -eu
candidate_user='${var.ssh_username}'
candidate_key='${local.ssh_public_key}'
candidate_home="$(getent passwd "$candidate_user" | cut -d: -f6)"
test -n "$candidate_home"
candidate_uid="$(id -u "$candidate_user")"
candidate_gid="$(id -g "$candidate_user")"
install -d -m 700 -o "$candidate_uid" -g "$candidate_gid" "$candidate_home/.ssh"
touch "$candidate_home/.ssh/authorized_keys"
chown "$candidate_uid:$candidate_gid" "$candidate_home/.ssh/authorized_keys"
chmod 600 "$candidate_home/.ssh/authorized_keys"
if ! grep -Fqx -- "$candidate_key" "$candidate_home/.ssh/authorized_keys"; then
  printf '%s\n' "$candidate_key" >> "$candidate_home/.ssh/authorized_keys"
fi
EOT

  # No Windows a conta administrativa NÃO lê o ``authorized_keys`` do home: o
  # Win32-OpenSSH exige ``administrators_authorized_keys`` em ProgramData, com
  # ACL restrita a Administrators e SYSTEM — chave no home de admin é
  # silenciosamente ignorada, e o banco ficaria de pé e inalcançável. O sshd já
  # vem habilitado da dourada (o compilador desta plataforma instala o
  # OpenSSH.Server e o deixa Automatic), então aqui só se autoriza a chave.
  candidate_user_data_windows = <<-EOT
<powershell>
$ErrorActionPreference = 'Stop'
$candidateUser = '${var.ssh_username}'
$candidateKey  = '${local.ssh_public_key}'
if (-not (Get-LocalUser -Name $candidateUser -ErrorAction SilentlyContinue)) {
  throw "candidate account $candidateUser does not exist in this image"
}
$isAdmin = $false
foreach ($member in (Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue)) {
  if ($member.Name -eq "$env:COMPUTERNAME\$candidateUser") { $isAdmin = $true }
}
if ($isAdmin) {
  $target = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
} else {
  $target = Join-Path 'C:\Users' $candidateUser
  $target = Join-Path $target '.ssh\authorized_keys'
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
if (-not (Test-Path $target)) { New-Item -ItemType File -Force -Path $target | Out-Null }
if (-not (Select-String -Path $target -SimpleMatch -Pattern $candidateKey -Quiet)) {
  # UTF-8 SEM BOM, escrito pelo .NET de proposito. No Windows PowerShell 5.1 o
  # Add-Content grava ANSI e o -Encoding utf8 grava COM BOM; o sshd rejeita o
  # authorized_keys com BOM, e o sintoma seria a candidata de pe e muda, sem
  # nada dizendo o porque.
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::AppendAllText($target, ($candidateKey + "`n"), $utf8NoBom)
}
if ($isAdmin) {
  icacls.exe $target /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
}
</powershell>
<persist>false</persist>
EOT

  candidate_user_data = (
    var.candidate_bootstrap == "windows_powershell"
    ? local.candidate_user_data_windows
    : local.candidate_user_data_posix
  )
}

# Both must exist and share a VPC. A security group from another VPC is not a
# misconfiguration the API reports clearly — it fails late and obscurely.
data "aws_subnet" "candidate" {
  id = local.subnet_id
}

data "aws_security_group" "candidate" {
  id = local.security_group_id
}

resource "aws_instance" "candidate" {
  ami           = var.ami_id
  instance_type = var.instance_type
  user_data     = local.candidate_user_data

  subnet_id              = local.subnet_id
  vpc_security_group_ids = [local.security_group_id]

  lifecycle {
    precondition {
      condition     = data.aws_subnet.candidate.vpc_id == data.aws_security_group.candidate.vpc_id
      error_message = "Validation subnet and security group are in different VPCs."
    }

    # A garantia POSIX que a validação da variável tinha continua valendo — só
    # que agora amarrada ao dialeto declarado, em vez de aplicada a toda
    # plataforma. Conta fora da forma da própria família é erro de cadastro, e
    # aparece nomeado antes de a instância nascer.
    precondition {
      condition = (
        var.candidate_bootstrap != "posix_shell"
        || can(regex("^[a-z_][a-z0-9_-]*$", var.ssh_username))
      )
      error_message = "ssh_username must be a valid Linux account name for a posix_shell candidate."
    }
  }

  # Whether the machine gets a routable address is a property of the network it
  # lands on, not of this module: the collector reaches it from outside the VPC,
  # so on such a network an unreachable machine cannot be measured at all. The
  # answer travels with the subnet and the security group, from the same source.
  # Exposure stays a decision — the security group still admits only the three
  # known origins, so "addressable" is not "open".
  associate_public_ip_address = local.assign_public_address

  # IMDSv2 required. With IMDSv1 available, any SSRF on this machine reaches the
  # instance metadata with a plain GET and walks away with the role's
  # credentials. The demo exists to be poked at by a person, which is exactly
  # the situation where that matters.
  #
  # `instance_metadata_tags` stays OFF. The tags carry tenant, execution and
  # image lineage, and this machine runs software that has not been validated
  # yet — handing it its own governance identity through the metadata service
  # would let unvalidated code read who it belongs to. That identity is for the
  # controller to OBSERVE from outside, never for the candidate to consult.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # The candidate boots from a draft image and is then measured: whatever the
  # image carries ends up on this volume. Encrypted at rest by default, deleted
  # with the instance — nothing survives the throwaway machine.
  root_block_device {
    encrypted             = true
    delete_on_termination = true
  }

  # The identity a teardown filters on. These tags are the reason a destroy can
  # prove it is terminating THIS run's machine and nothing else, so they are not
  # decoration — they are the safety mechanism.
  tags = {
    Name                  = "${var.name_prefix}-${var.execution_id}"
    SamuraiOrganizationId = var.organization_id
    SamuraiExecutionId    = var.execution_id
    SamuraiImageVersionId = var.image_version_id
    managed_by            = "samurai-shield"
  }

  volume_tags = {
    Name                  = "${var.name_prefix}-${var.execution_id}"
    SamuraiOrganizationId = var.organization_id
    SamuraiExecutionId    = var.execution_id
    managed_by            = "samurai-shield"
  }
}

# ── Outputs consumed by the image_validate role ────────────────────────────
output "instance_id" { value = aws_instance.candidate.id }

# The address the collector is expected to use, which is not always the private
# one: on a network the collector reaches from outside, publishing the private
# address would register a target nobody can open a session to. Whoever consumes
# this wants a reachable machine, so the reachable address is what it answers.
output "ip_address" {
  value = (
    aws_instance.candidate.public_ip != ""
    ? aws_instance.candidate.public_ip
    : aws_instance.candidate.private_ip
  )
}

output "private_ip" { value = aws_instance.candidate.private_ip }
output "region" { value = var.region }
output "state" { value = aws_instance.candidate.instance_state }
