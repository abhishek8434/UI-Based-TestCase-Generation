from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from config.settings import MONGODB_URI, MONGODB_DB
import uuid
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class MongoHandler:
    def __init__(self):
        try:
            self.client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
            # Verify connection
            self.client.server_info()
            self.db = self.client[MONGODB_DB]
            self.collection = self.db.test_cases
            logger.info("Successfully connected to MongoDB")
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise Exception("Could not connect to MongoDB. Please check your connection settings.")

    def save_test_case(self, test_data):
        """Save test case data and generate unique URL"""
        try:
            unique_id = str(uuid.uuid4())
            document = {
                "_id": unique_id,
                "test_data": test_data,
                "created_at": datetime.utcnow(),
                "url_key": unique_id
            }
            self.collection.insert_one(document)
            logger.info(f"Successfully saved test case with ID: {unique_id}")
            return unique_id
        except Exception as e:
            logger.error(f"Error saving test case: {str(e)}")
            raise Exception("Failed to save test case to database")

    def get_test_case(self, url_key):
        """Retrieve test case data by URL key"""
        try:
            result = self.collection.find_one({"url_key": url_key})
            if not result:
                logger.warning(f"No test case found for URL key: {url_key}")
            return result
        except Exception as e:
            logger.error(f"Error retrieving test case: {str(e)}")
            raise Exception("Failed to retrieve test case from database")