################################################################################
# SamurAI Shield :: Legacy-to-Immutable Rebuild :: generated baseline
# variables.tf — declared inputs, no secrets.
################################################################################

variable "subscription_id" {
  description = "Azure subscription ID where the VM lives."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the legacy VM."
  type        = string
  default     = "Samurai"
}

variable "vm_name" {
  description = "Name of the (re)built VM."
  type        = string
  default     = "vm1-samurai"
}

variable "vm_size" {
  description = "Azure VM size (e.g. Standard_B2s)."
  type        = string
  default     = "Standard_B1ms"
}

variable "os_disk_name" {
  description = "Name of the existing OS managed disk to reuse."
  type        = string
  default     = "vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47"
}

variable "public_ip_name" {
  description = "Name of the preserved Public IP."
  type        = string
  default     = "vm-samurai-ip"
}

variable "nic_name" {
  description = "Name of the preserved NIC."
  type        = string
  default     = "vm1-samurai814_z1"
}

variable "subnet_id" {
  description = "Subnet ID where the NIC is attached. TODO: review."
  type        = string
  default     = "/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/virtualNetworks/vm1-samurai-vnet/subnets/default"
}

variable "private_ip" {
  description = "Static private IP address of the NIC."
  type        = string
  default     = "10.1.0.4"
}

variable "nsg_id" {
  description = "Network Security Group ID associated with the NIC. TODO: review rules."
  type        = string
  default     = "/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkSecurityGroups/vm1-samurai-nsg"
}

variable "admin_username" {
  description = <<EOT
    Required by azurerm_(linux|windows)_virtual_machine. The legacy OS disk
    is reused as-is, so this value is effectively unused at create time.
    Provide a non-secret placeholder; never set admin_password here.
    TODO: review.
  EOT
  type    = string
  default = "samurai-shield"
}

variable "tags" {
  description = "Tags applied to all created/managed resources."
  type        = map(string)
  default     = {
    "samurai_shield_origin" = "legacy-to-immutable-rebuild"
    "samurai_shield_baseline_at" = "2026-05-05T05:31:48Z"
  }
}
