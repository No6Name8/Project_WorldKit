# Real vs model accuracy

Ground truth: `pipeline\samples\ground_truth.json` (ruler measurements; evaluation only, never used to scale or tune). Model sizes: print STLs in mm, base plate excluded.

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 71.3 | +1.9 | coin (coin, 2.15 cm diameter, 4 frames) |
| mug | rim_diameter | 85 | 88.1 | +3.7 | coin (coin, 2.15 cm diameter, 4 frames) |
| mug | base_diameter | 65 | 63.8 | -1.9 | coin (coin, 2.15 cm diameter, 4 frames) |

Mean absolute error 2.5% over 3 dimensions; worst 3.7%.

Coin: no ruler measurement recorded in ground_truth.json; the preset diameter is a looked-up value.
