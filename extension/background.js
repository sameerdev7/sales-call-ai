let socket = null;
let activeTabId = null;

function connectWebSocket() {
    console.log("[BG] Connecting to backend...");

    socket = new WebSocket("ws://127.0.0.1:8000/ws");

    socket.onopen = () => {
        console.log("[BG] Connected to backend");
    };

    socket.onmessage = (event) => {
        console.log("[BG] Backend:", event.data);

        try {
            const message = JSON.parse(event.data);

            console.log("[BG] Parsed message:", message);

            if (message.type === "summary") {
                console.log(
                    "[BG] Sending summary to tab:", 
                    activeTabId 
                );

                if (activeTabId === null) {
                    console.error("[BG] No active tab ID!");
                    return;
                }

                chrome.tabs.sendMessage(
                    activeTabId,
                    message,
                    () => {
                        if (chrome.runtime.lastError) {
                            console.error(
                                "[BG] sendMessage error:",
                                chrome.runtime.lastError.message 
                            ); 
                        } else {
                            console.log(
                                "[BG] Summary succesfully sent to content.js" 
                            );
                        }
                    }
                );
            }
        } catch (error) {
            console.error("[BG] Parse error:", error);
        }
};
    

    socket.onclose = () => {
        console.log("[BG] Disconnected");

        socket = null;

        setTimeout(connectWebSocket, 2000);
    };

    socket.onerror = (error) => {
        console.error("[BG] WebSocket error:", error);
    };
}

connectWebSocket();


chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

    if (message.type === "transcript") {

        activeTabId = sender.tab.id;

        console.log("[BG] Transcript received:", message.text);

        if (socket && socket.readyState === WebSocket.OPEN) {

            socket.send(JSON.stringify({
                type: "transcript",
                text: message.text,
                timestamp: message.timestamp
            }));

        } else {

            console.log("[BG] Backend not connected");

        }
    }
});