import os
import urllib.request
import xml.etree.ElementTree as ET

RSS_URL = "https://www.abc.net.au/feeds/101858056/podcast.xml" 
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

with urllib.request.urlopen(RSS_URL) as response:
    rss_data = response.read()

root = ET.fromstring(rss_data)
first_item = root.find("./channel/item")
enclosure = first_item.find("enclosure")
mp3_url = enclosure.get("url")

filename = mp3_url.split("/")[-1].split("?")[0]
os.makedirs(DATA_DIR, exist_ok=True)
filepath = os.path.join(DATA_DIR, filename)

urllib.request.urlretrieve(mp3_url, filepath)

