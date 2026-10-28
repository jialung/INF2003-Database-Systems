"""The MongoDB `places` collection: a read-only copy of bus stops (MySQL is the
system of record) plus MRT exits, kept for $geoNear / $geoWithin queries."""
import re


def station_key(name):
    """'JURONG EAST MRT STATION' and 'Jurong East' both become 'JURONG EAST'."""
    return re.sub(r"\s+(MRT|LRT)\s+STATION$", "", name.strip().upper())


def stop_id(bus_stop_code):
    return f"BS:{bus_stop_code}"


def stop_place(row):
    return {
        "_id": stop_id(row["bus_stop_code"]),
        "kind": "bus_stop",
        "ref_code": row["bus_stop_code"],
        "name": row["description"],
        "road_name": row["road_name"],
        "location": {"type": "Point", "coordinates": [float(row["longitude"]), float(row["latitude"])]},
    }


def exit_place(feature):
    props = feature["properties"]
    lon, lat = feature["geometry"]["coordinates"][:2]
    station = station_key(props["STATION_NA"]).title()
    return {
        "_id": f"EX:{props['STATION_NA'].strip()}:{props['EXIT_CODE'].strip()}",
        "kind": "mrt_exit",
        "ref_code": station,
        "name": f"{station} {props['EXIT_CODE'].strip()}",
        "location": {"type": "Point", "coordinates": [lon, lat]},
    }
