# Generated Terraform baseline — vm1-samurai

Generated at: 2026-05-05T05:31:48Z
Source: SamurAI Shield :: Legacy-to-Immutable Rebuild
Subscription: 8a46e231-771b-40d7-90f5-6a8e17682197
Resource group: Samurai

> ⚠️ This is a **generated baseline**. It represents the state of the
> Azure VM at the moment the baseline was collected. It is **not**
> production-ready as-is. Review every comment marked
> `# TODO: review` and adjust the inputs to match your tenant
> policies (NSG association, diagnostics, locks, custom extensions,
> governance tags) before running `terraform apply`.

## Files

- `main.tf` — resource definitions for the VM, OS disk reference,
  NIC, Public IP, NSG (when applicable) and data disks.
- `variables.tf` — declared variables (no secrets).
- `outputs.tf` — useful outputs (VM ID, NIC ID, Public IP address).
- `terraform.tfvars.example` — example values, populated from the
  collected baseline. Copy to `terraform.tfvars` and review before use.

## Security guarantees

- No secrets, passwords, tokens or private keys are present in any
  file in this directory.
- `admin_username` and `admin_password` are deliberately omitted:
  the baseline reuses the existing OS disk and does not require
  credential injection at create time.

## Manual review checklist

- [ ] NSG rules — replicate the rules from `security_facts.json`.
- [ ] Tags — confirm they match the governance taxonomy.
- [ ] VM extensions — if `security_facts.json/extensions` is non-empty,
      add `azurerm_virtual_machine_extension` resources.
- [ ] Data disk attachments — confirm `caching` and `lun` values.
- [ ] State backend — switch from local state to a remote backend.
