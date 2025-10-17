# Top-4 post-GW4 transfer export

This folder contains ready-to-upload payloads for the production S3 bucket
after applying the requested Top-4 transfers following GW4.

## Files

| File | Target S3 key | Notes |
| --- | --- | --- |
| `draft_state_top4.json` | `draft_state_top4.json` | Primary state file with updated rosters. |
| `lineups_round7.json` | `top4_lineups/<override_version>/round7.json` | Optional cache refresh (includes updated `raw_state`). |
| `gw4_transfer_summary.json` | — | Audit log only (not required on S3). |

The current lineups cache uses override version
`2025-09-override-v4-complete-logos-fix-results`; upload the file into that
prefix if you need to refresh cached lineups on S3.

## Transfers applied

| Manager | Out | In |
| --- | --- | --- |
| Сергей | Igor Zubeldia → Marcos Senesi |
| Ксана | Loïs Openda → Andrej Ilić |
| Руслан | Óscar Mingueza → Javi Rueda |
| Саша | Morgan Rogers → Jack Grealish |
| Андрей | Miguel Gutiérrez → Gleison Bremer |
| Макс | Sergio Gómez → Wesley França |
| Сергей | Ayoze Pérez → Ilyas Ansah |
| Женя | Dominic Solanke → Wilson Isidor |
| Ксана | Deniz Undav → Jamie Leweling |
| Руслан | Rayan Aït-Nouri → Marc Guéhi |
| Андрей | Jonathan David → Rômulo |
| Саша | Nick Woltemade → Serge Gnabry |
| Сергей | Robin Hack → Pablo Fornals |
| Женя | Romelu Lukaku → Vedat Muriqi |
| Макс | Pedro Porro → Cristian Romero |
| Андрей | Nadiem Amiri → Anthony Gordon |
| Макс | Yoane Wissa → Fisnik Asllani |
| Саша | Giuliano Simeone → Thiago Almada |
| Ксана | Tino Livramento → Dan Burn |

The transfer summary (`gw4_transfer_summary.json`) repeats the data above
with roster indices for convenience.
