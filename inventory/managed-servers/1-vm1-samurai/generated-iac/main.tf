################################################################################
# SamurAI Shield :: Legacy-to-Immutable Rebuild :: generated baseline
# ------------------------------------------------------------------------------
# Source VM:   vm1-samurai
# Subscription:8a46e231-771b-40d7-90f5-6a8e17682197
# Resource group: Samurai
# Generated at:2026-05-05T05:46:08Z
#
# WARNING: this is a generated baseline. Review every "TODO: review" comment
# below before running terraform apply against any environment. No secrets,
# passwords, tokens or private keys are embedded.
################################################################################

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.110"
    }
  }
  # TODO: review — switch to a remote state backend (azurerm/storage account).
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

# ------------------------------------------------------------------------------
# Resource group (referenced — the legacy VM lived here).
# ------------------------------------------------------------------------------
data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

# ------------------------------------------------------------------------------
# Existing OS managed disk (preserved by the rebuild flow).
# ------------------------------------------------------------------------------
data "azurerm_managed_disk" "os" {
  name                = var.os_disk_name
  resource_group_name = data.azurerm_resource_group.this.name
}

# ------------------------------------------------------------------------------
# Public IP — preserved (Static).
# ------------------------------------------------------------------------------
resource "azurerm_public_ip" "this" {
  name                = var.public_ip_name
  resource_group_name = data.azurerm_resource_group.this.name
  location            = data.azurerm_resource_group.this.location
  allocation_method   = "Static"
  sku                 = "Standard"

  tags = var.tags
}

# ------------------------------------------------------------------------------
# Network interface — preserved.
# TODO: review — confirm subnet_id and private IP allocation policy.
# ------------------------------------------------------------------------------
resource "azurerm_network_interface" "this" {
  name                = var.nic_name
  resource_group_name = data.azurerm_resource_group.this.name
  location            = data.azurerm_resource_group.this.location

  ip_configuration {
    name                          = "primary"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Static"
    private_ip_address            = var.private_ip
    public_ip_address_id          = azurerm_public_ip.this.id
  }

  tags = var.tags
}

# ------------------------------------------------------------------------------
# NSG association — the legacy VM had an NSG attached at the NIC level.
# TODO: review — replicate the NSG rules from baseline/security_facts.json.
# ------------------------------------------------------------------------------
resource "azurerm_network_interface_security_group_association" "this" {
  network_interface_id      = azurerm_network_interface.this.id
  network_security_group_id = var.nsg_id
}

# ------------------------------------------------------------------------------
# Virtual machine — attaches the existing OS managed disk.
# TODO: review — admin_username / SSH keys / WinRM are intentionally absent.
# This block reuses the existing OS disk so no admin credential is required.
# Replace with a fresh VM definition only after planning a controlled cutover.
# ------------------------------------------------------------------------------
resource "azurerm_linux_virtual_machine" "this" {
  name                = var.vm_name
  resource_group_name = data.azurerm_resource_group.this.name
  location            = data.azurerm_resource_group.this.location
  size                = var.vm_size

  network_interface_ids = [azurerm_network_interface.this.id]

  os_disk {
    name                 = data.azurerm_managed_disk.os.name
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 0
    # TODO: review — managed disk attachment by ID requires importing into
    # Terraform state or using azurerm_virtual_machine (legacy) for the
    # `attach` workflow. azurerm_linux_virtual_machine creates a fresh OS
    # disk by default. For a 1:1 reuse of the existing OS disk, prefer the
    # `azurerm_virtual_machine` resource block (legacy) or import this
    # resource and remove the os_disk creation block.
  }

  # TODO: review — admin_username is required by the provider but unused
  # when reusing an existing OS disk. Provide a non-secret placeholder.
  admin_username = var.admin_username

  disable_password_authentication = true

  tags = var.tags
}

