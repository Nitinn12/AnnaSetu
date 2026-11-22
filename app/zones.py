"""Zones and organisation types for the AnnaSetu demo.

All zones, coordinates and names here are fictional (synthetic). They do not
correspond to real places and must not be used as real pickup locations.
"""

ZONES = [
    ("Z-01", "Zone 1 (synthetic)"),
    ("Z-02", "Zone 2 (synthetic)"),
    ("Z-03", "Zone 3 (synthetic)"),
    ("Z-04", "Zone 4 (synthetic)"),
]

# Synthetic (latitude, longitude) per zone. Not real addresses.
ZONE_COORDS = {
    "Z-01": (12.9716, 77.5946),
    "Z-02": (12.9750, 77.6010),
    "Z-03": (12.9650, 77.5900),
    "Z-04": (12.9800, 77.5850),
}

ORG_TYPES = [
    ("canteen", "College canteen"),
    ("hostel_mess", "Hostel mess"),
    ("ngo", "NGO"),
    ("community_kitchen", "Community kitchen"),
]
