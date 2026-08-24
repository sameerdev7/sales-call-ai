import os 

from dotenv import load_dotenv
from pymongo import MongoClient


load_dotenv()

client = MongoClient(os.getenv("MONGODB_URI"))

db = client[os.getenv("MONGODB_DB", "sales_call_ai")]

calls = db["calls"]