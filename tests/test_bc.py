"""Expert behavior cloning: fitted weights become priors, bounds widen to hold them, expertpick is not counted
twice, the seed migration applies once, the fit learns an obvious preference, upgrades follow the fit."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent"))

import expert_bc  # noqa: E402
from model import Model  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "expert_bc.json").write_text(json.dumps({"weights": {"card:Vault": 4.5, "skip:a2": 3.2},
                                                         "upgrade": {"Eruption": 2.8}}), encoding="utf-8")
        m = Model(root / "model.json", "WATCHER")
        assert m.prior("card:Vault") == 4.5, m.prior("card:Vault")
        assert m.prior("expertpick") == 0.0
        lo, hi = m.bounds("skip:a2")
        assert hi >= 3.2 and lo <= -1.5, (lo, hi)
        assert m.bc_upgrade["Eruption"] == 2.8
        # learning moves a fitted weight at BC_LR of the normal rate
        ev = {"kind": "card", "options": [{"card:Vault": 1.0}, {"card:Other": 1.0}], "chosen": 1,
              "probs": [0.5, 0.5], "temp": 1.0, "act": 1}
        m.meta["act_baseline"] = {"1": {"b": 0.0, "var": 0.04}}
        m.learn([ev], 0.5, lr=0.1)
        moved_bc = 4.5 - m.weights["card:Vault"]
        moved_other = m.weights["card:Other"] - m.prior("card:Other")
        assert 0 < moved_bc < moved_other, (moved_bc, moved_other)
        m2 = Model(root / "none" / "model.json", "WATCHER")
        assert m2.prior("expertpick") == 3.0 and not m2.bc
    # the fit recovers a preference: A is always taken over B
    data = [("r%d" % i, [{"card:A": 1.0}, {"card:B": 1.0}], 0, [0.0, 0.0]) for i in range(50)]
    w = expert_bc.fit(data, {}, {}, l2=1.0, epochs=100)
    assert w["card:A"] > w["card:B"] + 1.0, w
    assert expert_bc.evaluate(data, w)[0] == 1.0
    print("BC OK")


if __name__ == "__main__":
    main()
