"""A voter model on a ring, run through ABMfact's command adapter.

Reads {"params", "seed", "knobs"} as JSON on stdin and prints a JSON object of outputs.
Any language works the same way; this one only needs the Python standard library.
"""

import json
import random
import sys


def main() -> None:
    job = json.load(sys.stdin)
    p = {"n": 200, "steps": 2000, "p_red": 0.6, **job["params"]}
    rng = random.Random(job["seed"])
    n = int(p["n"])
    opinions = [1 if rng.random() < p["p_red"] else 0 for _ in range(n)]
    for _ in range(int(p["steps"])):
        i = rng.randrange(n)
        j = (i + rng.choice((-1, 1))) % n
        opinions[i] = opinions[j]
    red = sum(opinions) / n
    json.dump({"final_red": red, "consensus": float(red in (0.0, 1.0))}, sys.stdout)


if __name__ == "__main__":
    main()
