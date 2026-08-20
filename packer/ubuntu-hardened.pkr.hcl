packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

# ── Inputs — see packer/README.md for the full contract ─────────────────────
# base_image_filter maps 1:1 to the `image_build.base_image` variable the
# Execution Control Plane preflight validates before this build is ever
# launched (apps/remediation-engine/services/preflight_validator.py).

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "vpc_id" {
  type    = string
  default = "vpc-026c893e40771455a"
}

variable "subnet_id" {
  type    = string
  default = "subnet-0bc5d4112bfb5e1ed"
}

# Endereçamento da máquina temporária de build. O destino escolhido pelo
# cliente decide o valor; o padrão preserva o comportamento anterior, em que
# este template pedia endereço público sempre.
variable "associate_public_ip_address" {
  type    = bool
  default = true
}

# Imagem base: UM nome para o filtro e UM nome para o dono.
#
# `base_image_filter` é o único nome do filtro de nome — o entrypoint do Cloud
# Executor o injeta com `-var` em TODO build, e `-var` de variável não declarada
# é erro duro no Packer. O campo `ami_name_filter` do cadastro da integração de
# destino chega AQUI (`DESTINATION_CONFIG_PACKER_VARIABLES`), sem virar uma
# segunda variável: duas fontes para o mesmo filtro é o vão que este template
# não vai abrir. O valor aprovado no plano (`image_build.base_image`) vence o
# cadastro; o cadastro só preenche quando o plano não traz nada.
variable "base_image_filter" {
  type        = string
  default     = "ubuntu/images/hvm-ssd-gp3/ubuntu-jammy-22.04-amd64-server-*"
  description = "AMI name filter for the base image (official Ubuntu 22.04 LTS AMIs by default). Receives the destination integration's ami_name_filter when the plan carries no base image."
}

# O dono da imagem base. Era fixo no `source_ami_filter` (`owners = [...]`), e o
# `ami_owner_id` do cadastro atravessava a allowlist para ser DESCARTADO EM
# SILÊNCIO — quem apontasse outro publicador construía sobre o Ubuntu achando
# que construía sobre o dele (issue #2219). O default é o valor que estava
# fixo, então build que não preenche o campo não muda de comportamento.
variable "ami_owner_id" {
  type        = string
  default     = "099720109477"
  description = "AWS account id that owns the base AMI (official Ubuntu publisher by default). Receives the destination integration's ami_owner_id."
}

variable "image_family_name" {
  type        = string
  default     = "unspecified"
  description = "Human-readable label for AWS tag traceability only. The Image Registry family that owns the resulting ImageVersion is resolved by image_family_id, which stays in the SamurAI Shield control plane and never reaches Packer/AAP (see packer/README.md)."
}

# Execution identity (samurai.image_build_evidence/v1 — plano L2I §10):
# every SamurAI Image Factory template MUST declare these two variables and
# tag the AMI with them. The image_build role always passes both as -var, and
# its post-build DescribeImages confirmation REFUSES an AMI missing the
# SamuraiExecutionId/SamuraiOrganizationId tags.

variable "samurai_execution_id" {
  type        = string
  default     = "unspecified"
  description = "Execution plan id that launched this build — becomes the SamuraiExecutionId AMI tag verified by the evidence contract."
}

variable "organization_id" {
  type        = string
  default     = "unspecified"
  description = "Tenant that owns this build — becomes the SamuraiOrganizationId AMI tag verified by the evidence contract."
}

# SBOM transport: the source exists only inside Packer's temporary build host.
# The directory is archived there and downloaded as one file because Packer's
# download provisioner cannot carry a directory as a local artifact. The
# executor expands that archive and runs Syft over the resulting rootfs tree.
variable "sbom_scan_target" {
  type        = string
  default     = "/var/lib/dpkg"
  description = "Directory (or legacy metadata file) on the temporary build host to export for SBOM generation."
}

variable "sbom_export_dir" {
  type        = string
  description = "Directory in the AAP Execution Environment that receives the downloaded SBOM source."
}

source "amazon-ebs" "ubuntu-hardened" {
  ami_name      = "samurai-shield-ubuntu-hardened-{{timestamp}}"
  instance_type = var.instance_type
  region        = var.aws_region
  vpc_id        = var.vpc_id
  subnet_id     = var.subnet_id
  associate_public_ip_address = var.associate_public_ip_address
  shutdown_behavior           = "terminate"

  source_ami_filter {
    filters = {
      name                = var.base_image_filter
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = [var.ami_owner_id]
  }

  ssh_username = "ubuntu"

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name                  = "samurai-shield-ubuntu-hardened"
    Family                = var.image_family_name
    BaseAMI               = "{{ .SourceAMI }}"
    BuildTime             = "{{ isotime }}"
    Project               = "samurai-shield"
    ManagedBy             = "samurai-image-factory"
    SamuraiExecutionId    = var.samurai_execution_id
    SamuraiOrganizationId = var.organization_id
  }
}

build {
  sources = ["source.amazon-ebs.ubuntu-hardened"]

  # Baseline patching — a golden image ships fully up to date, never "patch
  # on first boot".
  provisioner "shell" {
    inline = [
      "sudo apt-get update -y",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y",
      "sudo apt-get autoremove -y --purge",
    ]
  }

  # Baseline hardening: unattended security patching stays on after boot,
  # SSH accepts keys only. The actual CVE posture is certified by the scan
  # step the AAP wrapper runs after this build (packer/README.md) — this
  # provisioner only sets the hardening posture, it does not scan.
  provisioner "shell" {
    inline = [
      "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades apt-listchanges",
      "echo 'Unattended-Upgrade::Automatic-Reboot \"false\";' | sudo tee -a /etc/apt/apt.conf.d/50unattended-upgrades",
      "sudo systemctl enable unattended-upgrades",
      "sudo sed -i '/^PasswordAuthentication/d;/^#PasswordAuthentication/d' /etc/ssh/sshd_config",
      "echo 'PasswordAuthentication no' | sudo tee -a /etc/ssh/sshd_config",
      "sudo sed -i '/^PermitRootLogin/d;/^#PermitRootLogin/d' /etc/ssh/sshd_config",
      "echo 'PermitRootLogin no' | sudo tee -a /etc/ssh/sshd_config",
    ]
  }

  provisioner "shell" {
    environment_vars = ["SBOM_SCAN_TARGET=${var.sbom_scan_target}"]
    inline = [
      "test -d \"$SBOM_SCAN_TARGET\" || test -f \"$SBOM_SCAN_TARGET\"",
      "SBOM_ARCHIVE_PATH=\"/tmp/samurai-sbom-source-${var.samurai_execution_id}.tar.gz\"; SBOM_SOURCE_DIR=\"$SBOM_SCAN_TARGET\"; if [ -f \"$SBOM_SOURCE_DIR\" ]; then SBOM_SOURCE_DIR=\"$(dirname \"$SBOM_SOURCE_DIR\")\"; fi; ( umask 077; set -C; sudo tar -C / -czf - -- \"$SBOM_SOURCE_DIR\" > \"$SBOM_ARCHIVE_PATH\" ) && test -s \"$SBOM_ARCHIVE_PATH\" || { echo 'SBOM archive path already exists or archive creation failed'; exit 1; }",
    ]
  }

  # This direction matters: Packer copies from the temporary build host back
  # to the Execution Environment. The later Syft/Grype commands never inspect
  # a path that exists only on the temporary host.
  provisioner "file" {
    direction   = "download"
    source      = "/tmp/samurai-sbom-source-${var.samurai_execution_id}.tar.gz"
    destination = "${var.sbom_export_dir}/sbom-source.tar.gz"
  }

  # Cleanup for AMI — no build residue, no machine-id reuse across clones
  # launched from the same image.
  provisioner "shell" {
    inline = [
      "sudo apt-get clean",
      "sudo rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*",
      "sudo truncate -s 0 /etc/machine-id",
      "sudo cloud-init clean --logs || true",
    ]
  }
}
