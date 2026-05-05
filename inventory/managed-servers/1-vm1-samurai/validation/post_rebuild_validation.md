# Post-Rebuild Validation Report — vm1-samurai

**Generated at (UTC):** 2026-05-05T05:45:48Z
**Subscription:** `8a46e231-771b-40d7-90f5-6a8e17682197`
**Resource group:** `Samurai`
**Overall status:** **PASSED**

---

## Azure-side checks

| Check | Expected | Actual | Result |
| ----- | -------- | ------ | ------ |
| `vm_exists` | `exists` | `exists` | ✅ ok |
| `vm_running` | `running` | `VM running` | ✅ ok |
| `public_ip_preserved` | `172.206.227.150` | `172.206.227.150` | ✅ ok |
| `private_ip_preserved` | `10.1.0.4` | `10.1.0.4` | ✅ ok |
| `nic_preserved` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkInterfaces/vm1-samurai814_z1` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkInterfaces/vm1-samurai814_z1` | ✅ ok |
| `os_disk_preserved` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/SAMURAI/providers/Microsoft.Compute/disks/vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/SAMURAI/providers/Microsoft.Compute/disks/vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47` | ✅ ok |
| `data_disks_count` | `0` | `0` | ✅ ok |

## OS-side reachability

- Reachable: **yes**
- Hostname: `vm1-samurai`
- FQDN: `vm1-samurai.internal.cloudapp.net`
- Kernel: `4.18.0-553.115.1.el8_10.x86_64`
- Failed services (post-rebuild): 0

## Diff summary (baseline → post-rebuild)

| Field | Before | After |
| ----- | ------ | ----- |
| `data_disks_count` | `0` | `0` |
| `location` | `eastus` | `eastus` |
| `nic_id` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkInterfaces/vm1-samurai814_z1` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/Samurai/providers/Microsoft.Network/networkInterfaces/vm1-samurai814_z1` |
| `os_disk_id` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/SAMURAI/providers/Microsoft.Compute/disks/vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47` | `/subscriptions/8a46e231-771b-40d7-90f5-6a8e17682197/resourceGroups/SAMURAI/providers/Microsoft.Compute/disks/vm1-samurai_OsDisk_1_2d9c0ae602e947aebbeb03c4d4eb1d47` |
| `os_type` | `Linux` | `Linux` |
| `private_ip` | `10.1.0.4` | `10.1.0.4` |
| `public_ip` | `172.206.227.150` | `172.206.227.150` |
| `tags` | `{}` | `{}` |
| `vm_size` | `Standard_B1ms` | `Standard_B1ms` |

---

## Interpretation

- `passed` — every Azure-side check matched and the OS responded.
- `partial` — Azure-side checks passed but the OS did not respond. The
  rebuild may still be successful; SSH/WinRM may need a retry or a security
  group/NSG fix.
- `failed` — at least one Azure-side check did not match. Use the
  snapshots from `snapshots/snapshots.json` to roll back if necessary.
