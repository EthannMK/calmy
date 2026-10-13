"""Built-in sample schedule. Used only when neither the live service nor the saved real schedule can answer.

It keeps flight search working on stage if the network is down. The flights are sample services on real routes
and are labelled as a sample schedule wherever the source is shown. Nothing here is live status.
"""

_CITY = {"BKK": "Bangkok", "DMK": "Bangkok Don Mueang", "HKT": "Phuket", "CNX": "Chiang Mai", "USM": "Koh Samui", "SIN": "Singapore",
         "KUL": "Kuala Lumpur", "HKG": "Hong Kong", "NRT": "Tokyo", "ICN": "Seoul", "LHR": "London", "JFK": "New York",
         "SYD": "Sydney", "CGK": "Jakarta", "DPS": "Bali", "DXB": "Dubai", "DEL": "Delhi", "CDG": "Paris", "FRA": "Frankfurt"}
_TZ = {"BKK": "Asia/Bangkok", "DMK": "Asia/Bangkok", "HKT": "Asia/Bangkok", "CNX": "Asia/Bangkok", "USM": "Asia/Bangkok", "SIN": "Asia/Singapore",
       "LHR": "Europe/London", "JFK": "America/New_York", "SYD": "Australia/Sydney", "DEL": "Asia/Kolkata", "DXB": "Asia/Dubai",
       "KUL": "Asia/Kuala_Lumpur", "CGK": "Asia/Jakarta", "DPS": "Asia/Makassar", "HKG": "Asia/Hong_Kong", "NRT": "Asia/Tokyo", "ICN": "Asia/Seoul", "CDG": "Europe/Paris", "FRA": "Europe/Berlin"}
_MARKET = {"BKK": "TH", "DMK": "TH", "HKT": "TH", "CNX": "TH", "USM": "TH", "SIN": "SG", "KUL": "GLOBAL", "LHR": "UK", "JFK": "US", "SYD": "AU",
           "DEL": "IN", "NRT": "JP", "CDG": "EU", "FRA": "EU"}

# airline, code prefix, origin, destination, local departure times, first flight number
_ROUTES = [
    ("Thai Airways", "TG", "BKK", "HKT", ["06:30", "09:15", "13:40", "18:20"], 201),
    ("Thai Airways", "TG", "BKK", "CNX", ["07:05", "11:30", "16:45", "20:10"], 105),
    ("Thai AirAsia", "FD", "DMK", "HKT", ["06:50", "10:25", "15:10", "19:35"], 3001),
    ("Thai AirAsia", "FD", "DMK", "CNX", ["07:20", "12:15", "17:40"], 3101),
    ("Bangkok Airways", "PG", "BKK", "USM", ["07:45", "11:10", "14:55", "18:30"], 931),
    ("Thai Airways", "TG", "BKK", "SIN", ["08:05", "12:40", "17:15", "21:30"], 403),
    ("Singapore Airlines", "SQ", "BKK", "SIN", ["09:40", "14:20", "19:05"], 971),
    ("Thai Airways", "TG", "BKK", "KUL", ["07:30", "13:05", "18:45"], 417),
    ("Thai Airways", "TG", "BKK", "HKG", ["08:50", "13:25", "20:00"], 601),
    ("Thai Airways", "TG", "BKK", "NRT", ["07:10", "23:25"], 640),
    ("Thai Airways", "TG", "BKK", "ICN", ["08:20", "22:35"], 658),
    ("Thai Airways", "TG", "BKK", "LHR", ["00:20", "12:30"], 910),
    ("Singapore Airlines", "SQ", "SIN", "BKK", ["07:25", "12:10", "16:50", "21:15"], 972),
    ("Singapore Airlines", "SQ", "SIN", "LHR", ["01:05", "09:30", "23:55"], 322),
    ("Singapore Airlines", "SQ", "SIN", "KUL", ["07:10", "10:40", "14:15", "18:50", "21:30"], 100),
    ("Singapore Airlines", "SQ", "SIN", "CGK", ["08:25", "12:05", "16:30", "20:15"], 950),
    ("Singapore Airlines", "SQ", "SIN", "HKG", ["07:55", "11:30", "15:40", "22:10"], 890),
    ("Singapore Airlines", "SQ", "SIN", "DPS", ["08:50", "13:10", "18:25"], 940),
    ("Singapore Airlines", "SQ", "SIN", "SYD", ["09:05", "17:40", "23:15"], 231),
    ("Singapore Airlines", "SQ", "SIN", "NRT", ["07:45", "23:10"], 636),
    ("British Airways", "BA", "LHR", "BKK", ["11:15", "20:40"], 9),
    ("British Airways", "BA", "LHR", "JFK", ["08:00", "12:05", "17:30"], 117),
    ("Emirates", "EK", "LHR", "DXB", ["08:15", "14:25", "21:05"], 8),
    ("Qantas", "QF", "SYD", "SIN", ["09:10", "21:25"], 1),
    ("Air India", "AI", "DEL", "BKK", ["06:20", "13:50"], 332),
    ("Delta", "DL", "JFK", "LHR", ["19:00", "21:30"], 1),
]


def build() -> dict:
    """The sample schedule in the same shape as the saved real schedule file."""
    flights = []
    for airline, pre, o, d, times, n0 in _ROUTES:
        for i, t in enumerate(times):
            flights.append({"code": f"{pre} {n0 + i * 2}", "airline": airline, "origin": o, "origin_city": _CITY[o], "dest": d,
                            "dest_city": _CITY[d], "time": t, "tz": _TZ[o], "market": _MARKET.get(o, "GLOBAL")})
    return {"generated_at": None, "flights": flights, "builtin": True}
