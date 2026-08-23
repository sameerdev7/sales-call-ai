import os 
import json 
import time

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from google import genai

load_dotenv()

app = FastAPI()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

@app.get("/")
def root():
    return {"status": "Sales Call AI Backend Running."}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    
    await websocket.accept()
    
    print("Chrome extension connected.")
    
    previous_summary = ""
    
    try:
        while True:
            message = await websocket.receive_json()
            
            text = message.get("text", "")
            
            print("\nRecieved transcript:")
            print(text)
            
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite", 
                contents=f"""
                You are a real-time meeting summarization assistant. 
                
                Update the running summary of the conversation. 
                
                Previous summary: 
                {previous_summary}
                
                New conversation:
                {text}
                
                Instructions:
                - Update the previous summary using the new conversation.
                - Preserve the important context from the previous summary. 
                - Focus on what is actually being discussed. 
                - Capture the overall flow and substance of the conversation. 
                - Do not extract information into categories. 
                - Do not invent or assume information. 
                - Remove irrelevant conversational filler. 
                - Keep the summary concise enough for small live widget. 
                - Return ONLY the updated natural-language-summary.
                """
                
            )
            
            print("\nGemini:")
            print(response.text)
            
            previous_summary = response.text
            
            await websocket.send_json({
                "type": "summary", 
                "summary": previous_summary, 
                "timestamp": int(time.time() * 1000)
            }) 
            
    except Exception as e:
        print("WebSocket disconnected: ", e)   