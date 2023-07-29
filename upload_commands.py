import json
import firebase_admin
from firebase_admin import firestore

APP = firebase_admin.initialize_app()

firestore_client = firestore.client()

# Load the commands JSON file
with open('actions_spodkest/actions.json', 'r') as file:
    commands_data = json.load(file)

# Save the JSON data to the 'commands/' collection
for command, content in commands_data.items():
    firestore_client.collection(u'publicActions').document(command).set(content)
