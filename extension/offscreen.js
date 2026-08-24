let mediaStream = null;
let mediaRecorder = null; 

let audioChunks = []; 

// Start Recording 
async function startRecording(streamId) {
    console.log("[AUDIO] Starting recording..."); 

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                mandatory: {
                    chromeMediaSource: "tab", 
                    chromeMediaSourceId: streamId 
                }
            }, 
            video: false 
        });
        console.log("[AUDIO] Media stream acquired.");

        audioChunks = [];

        mediaRecorder = new MediaRecorder(
            mediaStream, 
            {
                mimeType = "audio/webm;codecs=opus" 
            }
        );

        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                audioChunks.push(event.data);
            }
            console.log("[AUDIO] Chunk:", event.data.size, "bytes");
        }

        mediaRecorder.onstart = () => {
            console.log("[AUDIO] MediaRecorder started"); 

            chrome.runtime.sendMessage({
                type: "audio_recording_startd"
            });
        };

        mediaRecorder.onerror = (event) => {
            console.error(
                "[AUDIO] MediaRecorder error:", event.error 
            );
        };

        mediaRecorder.onstop = async () => {
            console.log("[AUDIO] MediaRecorder stopped");

            const blob = new Blob(audioChunks, {
                type: "audio/webm"
            });

            console.log("[AUDIO] Final recording:", blob.size, "bytes");

            const arrayBuffer = await blob.arrayBuffer();

            chrome.runtime.sendMessage({
                type: "audio_recording_complete",
                audioBuffer: arrayBuffer
            });

            // Cleanup 
            audioChunks = [];

            if (mediaStream) {
                mediaStream.getTracks().forEach(track => track.stop());
            }

            mediaStream = null;
            mediaRecorder = null;
        };

    mediaRecorder.start(
        1000 
    );

    } catch (error) {
        console.error("[AUDIO] Failed to start.", error);

        chrome.runtime.sendMessage({
            type: "audio_recoding_error",
            error: error.message 
        });
    }

} 


// Stop Recoding 
function stopRecording() {
    console.log("[AUDIO] Stop requested."); 

    if (mediaRecorder && mediaRecorder.state != "inactive") {
        mediaRecorder.stop();
    } else {
        console.log("[AUDIO] Recorder is not active");
    }
}

// Messages 
chrome.runtime.onMessage.addListener(
    (message) => {
        if (message.type === "start_audio_recording") {
            startRecording(message.streamId);
        }

        if (message.type === "stop_audio_recording") {
            stopRecording();
        }
    }
);