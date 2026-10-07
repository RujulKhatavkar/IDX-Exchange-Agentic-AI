import sys
from pathlib import Path

import pytest

# Test the copy in this repo: skills/idx-property-search
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "idx-property-search"))
from parse_query import parse_property_query  # noqa: E402

# Small stand in for cities.txt so tests run without the database.
CITIES = ["Irvine", "Newport Beach", "Newport", "San Diego", "Pasadena", "Riverside", "Los Angeles", "Big Bear"]

CASES = [
    (
        "Show me 3-bedroom condos in Irvine under $1.5M with a pool.",
        {"city": "Irvine", "beds": 3, "type": "Condominium", "maxPrice": 1_500_000, "pool": "True"},
    ),
    (
        "single family homes in Newport Beach under 2.5 million",
        {"city": "Newport Beach", "type": "SingleFamilyResidence", "maxPrice": 2_500_000},
    ),
    (
        "4 bed 2.5 bath house in Pasadena over $900k",
        {"city": "Pasadena", "beds": 4, "baths": 2.5, "minPrice": 900_000},
    ),
    (
        "townhomes in San Diego between 800k and 1.2M",
        {"city": "San Diego", "type": "Townhouse", "minPrice": 800_000, "maxPrice": 1_200_000},
    ),
    (
        "at least 3 beds and 2 baths in Riverside, budget of $650,000",
        {"city": "Riverside", "beds": 3, "baths": 2, "maxPrice": 650_000},
    ),
    (
        "condo with ocean view and HOA under $500 in Newport Beach",
        {"city": "Newport Beach", "type": "Condominium", "hasView": "True", "maxHoa": 500, "maxPrice": None},
    ),
    (
        "2,000 sq ft homes in Irvine under 1.8m",
        {"city": "Irvine", "sqft": 2000, "maxPrice": 1_800_000},
    ),
    (
        "3br 2ba no pool Los Angeles max $1,100,000",
        {"city": "Los Angeles", "beds": 3, "baths": 2, "pool": "False", "maxPrice": 1_100_000},
    ),
    (
        "duplex near Riverside under 900k",
        {"city": "Riverside", "type": "Duplex", "maxPrice": 900_000},
    ),
    (
        "cabin in Big Bear under $600k",
        {"city": "Big Bear", "type": "Cabin", "maxPrice": 600_000},
    ),
    (
        "manufactured home in Riverside with no HOA",
        {"city": "Riverside", "type": "ManufacturedOnLand", "maxHoa": 0},
    ),
    (
        "homes with mountain views and no HOA in Pasadena",
        {"city": "Pasadena", "hasView": "True", "maxHoa": 0},
    ),
    (
        "5 bedroom single-family with pool and view, under $3M",
        {"city": None, "beds": 5, "type": "SingleFamilyResidence", "pool": "True", "hasView": "True", "maxPrice": 3_000_000},
    ),
    (
        "find me something cheap",
        {"city": None, "beds": None, "maxPrice": None, "type": None},
    ),
]


@pytest.mark.parametrize("query,expected", CASES, ids=[c[0][:40] for c in CASES])
def test_known_cities(query, expected):
    result = parse_property_query(query, cities=CITIES)
    for key, value in expected.items():
        assert result[key] == value, f"{key}: got {result[key]!r}, expected {value!r}"


@pytest.mark.parametrize(
    "query,city",
    [
        ("3 bed homes in Irvine under $1M", "Irvine"),
        ("condos in newport beach with a pool", "Newport Beach"),
        ("anything in Rancho Cucamonga below 700k", "Rancho Cucamonga"),
    ],
)
def test_city_fallback_without_list(query, city):
    assert parse_property_query(query, cities=[])["city"] == city
