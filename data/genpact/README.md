# Genpact food demand dataset

FoodWise's real-data benchmark reads three files from this folder:

- `train.csv`: weekly orders per fulfilment centre and meal, weeks 1 to 145
- `meal_info.csv`: meal category and cuisine
- `fulfilment_center_info.csv`: centre city, region, type and area

`test.csv` is not needed. It has no orders, so it cannot be scored.

## Getting the files

1. Sign in at kaggle.com and search Datasets for "Food Demand Forecasting". It is the Genpact
   Machine Learning Hackathon data from Analytics Vidhya (2018).
2. Download the zip and unzip the CSVs into this folder.

The files are not committed to git (see `.gitignore`). Check the licence on the Kaggle page
before redistributing them.

## Running the benchmark

From the app: Learn > Real-data benchmark > Run benchmark.

From a terminal:

```bash
cd backend && uv run foodwise benchmark
```

With Docker:

```bash
docker compose exec api foodwise benchmark
```
