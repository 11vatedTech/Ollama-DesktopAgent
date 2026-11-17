# Session 8 evidence (2026-11-16)

Raw result files behind "Session 8" in `../../VERIFICATION_EVIDENCE.md`, copied
unchanged from the `11vated accept --output` and `11vated qualify --json` runs.
Progress logs, stderr captures and the model profile (which contained local
filesystem paths) are not included.

Host: RTX 5070 Ti Laptop GPU (12 227 MiB VRAM), Windows 11 (10.0.26200), Ollama
0.40.16, Python 3.12. Models: `abliterant/qwen3.5-rana:latest`, `qwen3.5:9b`
(both 9B, Q4_K_M). Window: 16 384 tokens unless named otherwise.

| File | What it is |
|---|---|
| `acceptance/accept-H-rana-{1,3,4,5}.json`, `accept-HJ-rana-3.json` | Scenario H on rana with the 16k window (5 runs); the last file also holds scenario J (6 hostile runs) |
| `acceptance/accept-H-qwen35-{1,2,3}.json`, `accept-J-qwen35.json` | Scenario H x3 and J on qwen3.5:9b |
| `acceptance/accept-I-rana-{1,2}.json`, `accept-I-qwen35-1.json` | Scenario I; `accept-I-rana-1` is the run where the model verifier returned PASS and the host checks failed |
| `acceptance/ablateF-H-{1,2}.json` | Ablation: H with the pre-fix configuration (4096-token window, 240 000-character compaction budget) |
| `acceptance/ablate-H-4096-{1,2}.json` | 4096 window with the new budget: the run fails closed with `context_limit` |
| `qualification/*.json` | Harness qualification records (2 trials per task); the id is in the file name |

There is no `accept-H-rana-2`: that index was not used. Ablations were produced by a
scratch wrapper that changed only the runtime's default window and compaction
budget; it is not part of the product.

## SHA-256

```
1f4ebbf647cfc71d44c3467c38bfd86c50659e0dd9122d5eb3eaa0a40ef33728 *acceptance/ablate-H-4096-1.json
3737f0d93869d621cf2f9dc3e8d2585f24e491a7fe6353e2391ee52f8a16f6c7 *acceptance/ablate-H-4096-2.json
ebca153cbe29f0612b2e344ee79abace1f1a8b7a13cc7c910a71eda811857295 *acceptance/ablateF-H-1.json
cc9b1539b81ce6b360a4f7b594c6e72bf9ff324d6dd595a9ff58a12484f850dc *acceptance/ablateF-H-2.json
ef73516c14ed5d0eadcbfd800c3625c163f08aa7cda017759504eb5d7e2713a4 *acceptance/accept-H-qwen35-1.json
e642a6bb603a581f26c787105653b26a0d61309c739cbb4ae50a8ee63fda4be1 *acceptance/accept-H-qwen35-2.json
dcbc90b15d3d43896b52e83e41ef5b9a3843e7e0658796f0ab7a9e6f7a028f85 *acceptance/accept-H-qwen35-3.json
c35df50f024ec21cf50c2631b613391db401cd91da52cfb018f385263f6e8d4f *acceptance/accept-H-rana-1.json
16148b4e378f7190e38964e3c4dff2cfe5bd44a177030de6eaa38a38f7ce45d1 *acceptance/accept-H-rana-3.json
d7249bfe2025d360853025e08d0af2c8ba08fce11bf7ae23e31825b7270e86a0 *acceptance/accept-H-rana-4.json
0002ae84c455ef272d18541cf0c34ebbe1446f2dbc35d5ddb99d28c6bbd497c8 *acceptance/accept-H-rana-5.json
6b669c5d3a61bc29caa65ac8d0b75675c31eeac24f0a40ca9cc3adbc192202fc *acceptance/accept-HJ-rana-3.json
6fa1a3c38f29fd587b942914039cfe5908f51117ae725378d551764a6fb2c71b *acceptance/accept-I-qwen35-1.json
f8c89c1e4a6f2d808a27c183e82be8da9ebcb9bb0f630f720f514562ba198eb1 *acceptance/accept-I-rana-1.json
9051e1b414d53897e3615714f5e70da0513240e84388a5b970f394d1fbe99407 *acceptance/accept-I-rana-2.json
08b30828b442d2b75e95a1b494ebc329cb28c2e6742b75039ad90996d8997cba *acceptance/accept-J-qwen35.json
617a90080538e481accb5421f05c2fb7b571fa69a6afe3d787a3e0a72cf31292 *qualification/qwen3.5-9b-computer-99cb23ae.json
0458ad601acdbe6f7760fd98739f5ca5a186d050b913929bf0c6be73737dfc58 *qualification/qwen3.5-9b-edit-926d1a80.json
0db422e37e851c22d37b99d9ca75286103993d1272b40b466bbf7bcc22dc080a *qualification/rana-edit-5c2454c4.json
```
