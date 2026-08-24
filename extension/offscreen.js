let mediaStream = null;
let mediaRecorder = null;

let audioChunks = [];

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    const chunkSize = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode.apply(
            null,
            bytes.subarray(i, i + chunkSize)
        );
    }
    return btoa(binary);
}

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

        mediaRecorder = new MediaRecorder(mediaStream, {
            mimeType: "audio/webm;codecs=opus"
        });

        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                audioChunks.push(event.data);
                console.log("[AUDIO] Chunk:", event.data.size, "bytes");
            }
        };

        mediaRecorder.onstart = () => {
            console.log("[AUDIO] MediaRecorder started");
            chrome.runtime.sendMessage({
                type: "audio_recording_started"
            });
        };

        mediaRecorder.onerror = (event) => {
            console.error("[AUDIO] MediaRecorder error:", event.error);
            chrome.runtime.sendMessage({
                type: "audio_recording_error",
                error: String(event.error)
            });
        };

        mediaRecorder.onstop = async () => {
            console.log("[AUDIO] MediaRecorder stopped");

            const blob = new Blob(audioChunks, {
                type: "audio/webm"
            });

            console.log("[AUDIO] Final recording:", blob.size, "bytes");

            const arrayBuffer = await blob.arrayBuffer();
            const audioBase64 = arrayBufferToBase64(arrayBuffer);

            chrome.runtime.sendMessage({
                type: "audio_recording_complete",
                audioBase64: audioBase64,
                size: blob.size
            });

            audioChunks = [];

            if (mediaStream) {
                mediaStream.getTracks().forEach(track => track.stop());
            }

            mediaStream = null;
            mediaRecorder = null;
        };

        mediaRecorder.start(1000);

    } catch (error) {
        console.error("[AUDIO] Failed to start.", error);

        chrome.runtime.sendMessage({
            type: "audio_recording_error",
            error: error.message
        });
    }
}

function stopRecording() {
    console.log("[AUDIO] Stop requested.");

    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    } else {
        console.log("[AUDIO] Recorder is not active");
    }
}

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
