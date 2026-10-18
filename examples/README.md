# Examples

| Folder | Adapter | What it shows |
|---|---|---|
| [zoo](zoo) | python | the clean reference model with every check testable; point it at a `fault_*` model to see a check catch its bug |
| [mesa_sir](mesa_sir) | mesa | a Mesa 3 model exposing two knobs through constructor keywords |
| [command](command) | command | a model in a separate process; checks it cannot support are reported as NOT_TESTABLE |

Run any of them from the repository root:

```bash
abmfact run examples/zoo/audit.yaml --out results/zoo
abmfact run examples/mesa_sir/audit.yaml --out results/mesa      # needs abmfact[mesa]
abmfact run examples/command/audit.yaml --out results/voter
```
