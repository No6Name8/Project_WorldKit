# Real vs model accuracy

Ground truth: `pipeline\samples\ground_truth.json` (ruler measurements; evaluation only, never used to scale or tune). Model sizes: print STLs in mm, base plate excluded.

| object | dimension | real mm | model mm | error % | model scale from |
|---|---|---:|---:|---:|---|
| mug | height | 70 | 71.3 | +1.9 | coin (coin, 2.15 cm diameter, 4 frames) |
| mug | rim_diameter | 85 | 88.1 | +3.7 | coin (coin, 2.15 cm diameter, 4 frames) |
| mug | base_diameter | 65 | 63.8 | -1.9 | coin (coin, 2.15 cm diameter, 4 frames) |

Mean absolute error 2.5% over 3 dimensions; worst 3.7%.

**Model objects with no ground-truth entry (unscored):** black glasses case (29 mm tall), red candle holder (221 mm tall), silver incense burner (326 mm tall), small water bottle (122 mm tall), striped mug (70 mm tall, scale reference)

Coin: no ruler measurement recorded in ground_truth.json; the preset diameter is a looked-up value.
