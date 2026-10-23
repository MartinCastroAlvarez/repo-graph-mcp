# Benchmarks

[`token_savings.py`](token_savings.py) compares the tokens an agent spends on a
question with the graph (the tool call plus its JSON answer) and without it
(`grep -rn` output plus every file it then reads in full). It is offline,
deterministic and uses a simple `\w+|punctuation` tokenizer, no API.

```bash
poetry run python benchmarks/token_savings.py                         # this repository
poetry run python benchmarks/token_savings.py --root ../app --question callers:handle --question impact:Order.save
poetry run python benchmarks/token_savings.py --json --min-ratio 5    # CI gate
```
