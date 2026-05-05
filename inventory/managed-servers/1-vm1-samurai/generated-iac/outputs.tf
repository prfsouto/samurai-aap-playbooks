################################################################################
# SamurAI Shield :: Legacy-to-Immutable Rebuild :: generated baseline
# outputs.tf — useful identifiers exposed by the module.
################################################################################

output "vm_id" {
  description = "Azure resource ID of the (re)built VM."
  value       = azurerm_linux_virtual_machine.this.id
}

output "nic_id" {
  description = "Azure resource ID of the preserved NIC."
  value       = azurerm_network_interface.this.id
}

output "public_ip_address" {
  description = "Static Public IP address preserved across the rebuild."
  value       = azurerm_public_ip.this.ip_address
}

output "os_disk_id" {
  description = "Azure resource ID of the OS managed disk reused by the rebuild."
  value       = data.azurerm_managed_disk.os.id
}

output "samurai_baseline_metadata" {
  description = "Provenance of this generated Terraform baseline."
  value = {
    generated_at        = "2026-05-05T05:31:48Z"
    source_subscription = "8a46e231-771b-40d7-90f5-6a8e17682197"
    source_resource_group = "Samurai"
    source_vm_name      = "vm1-samurai"
    flow                = "legacy-to-immutable-rebuild"
  }
}
