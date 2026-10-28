"""Minimal LTA DataMall client (https://datamall.lta.gov.sg)."""
import requests

from app.config import Config

BASE_URL = "https://datamall2.mytransport.sg/ltaodataservice/"
PAGE_SIZE = 500  # DataMall returns at most 500 records per call


class DataMall:
    def __init__(self, key=None):
        key = key or Config.DATAMALL_KEY
        if not key:
            raise SystemExit("DATAMALL_KEY is not set; add it to .env")
        self.session = requests.Session()
        self.session.headers.update({"AccountKey": key, "accept": "application/json"})

    def get(self, endpoint, **params):
        response = self.session.get(BASE_URL + endpoint, params=params, timeout=30)
        if response.status_code == 401:
            raise SystemExit("DataMall rejected the AccountKey (401). Check DATAMALL_KEY in .env: "
                             "no quotes or spaces, and use the key from DataMall's e-mail, not your password.")
        response.raise_for_status()
        return response.json()

    def get_all(self, endpoint):
        """Follows $skip paging until an empty page comes back."""
        records, skip = [], 0
        while True:
            page = self.get(endpoint, **{"$skip": skip})["value"]
            if not page:
                return records
            records.extend(page)
            skip += PAGE_SIZE

    def bus_arrival(self, bus_stop_code):
        return self.get("v3/BusArrival", BusStopCode=bus_stop_code)

    def passenger_volume_link(self, dataset, period):
        """dataset is one of PV/Bus, PV/ODBus, PV/Train, PV/ODTrain; period like '202409'.
        Returns a short-lived download link to a zipped CSV."""
        return self.get(dataset, Date=period)["value"][0]["Link"]
