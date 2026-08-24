

// Detecting the caption element

// const observer = new MutationObserver((mutations) => {
//   for (const mutation of mutations) {
//     for (const node of mutation.addedNodes) {
//       if (node.nodeType === Node.ELEMENT_NODE) {
//         console.log("New element: ", node);
//       }
//     }
//   }
// });
//
// observer.observe(document.body, {
//   childList: true, 
//   subtree: true
// })
//
console.log("Sales Call AI Loaded.");

/*
 * Meeting lifecycle:
 * Recording starts on the FIRST genuine caption
 * (architecture.md §8) and ends when the user
 * actually leaves the call (architecture.md §9).
 * beforeunload is only a fallback signal.
 */

let meetCallDetected = false;
let sessionEnded = false;

function startSession() {
    meetCallDetected = true;
    sessionEnded = false;

    chrome.runtime.sendMessage({
        type: "meet_session_started"
    });

    console.log("[MEET] Session started (first caption).");
}

function endSession() {
    if (sessionEnded || !meetCallDetected) {
        return;
    }

    sessionEnded = true;
    meetCallDetected = false;

    chrome.runtime.sendMessage({
        type: "meet_session_ended"
    });

    console.log("[MEET] Session ended.");
}

window.addEventListener("beforeunload", () => {
    endSession();
});

let previousCaption = "";
let sendTimer = null;

const SEND_DELAY = 1500;

const CAPTION_SELECTOR =
    "div.ygicle.VbkSUe";


function readCaption() {
    const captions = document.querySelectorAll(
        "div.ygicle.VbkSUe"
    );

    if (captions.length === 0) {
        return;
    }

    const latestCaption =
        captions[captions.length - 1];

    const currentCaption =
        latestCaption.innerText.trim();

    if (!currentCaption || currentCaption === previousCaption) {
        return;
    }

    console.log("[LIVE]", currentCaption);


    // Extract speaker
    const container =
    latestCaption.closest("div.nMcdL.bj4p3b");

    const speaker =
        container?.querySelector("span.NWpY1d")
            ?.innerText
            .trim() || "Unknown";

    console.log("[SPEAKER]", speaker);

    // Extract only new text 

    let newText = currentCaption;

    if (previousCaption && currentCaption.startsWith(previousCaption)) {
        newText = currentCaption.slice(previousCaption.length).trim(); 
    }

    previousCaption = currentCaption;

    if (!newText) {
        return;
    }

    console.log("[NEW TEXT]", newText);

    // First genuine caption starts the session (once).
    if (!meetCallDetected || sessionEnded) {
        startSession();
    }

    clearTimeout(sendTimer);

    sendTimer = setTimeout(() => {
        sendToBackend(
            newText, 
            speaker 
        );
    }, SEND_DELAY);
}


function processCaption(currentCaption) {

    let newText = currentCaption;

    /*
     * If Meet's current caption starts with
     * the previous caption, remove the previous
     * portion and keep only the new text.
     */
    if (currentCaption.startsWith(previousCaption)) {
        newText = currentCaption
            .slice(previousCaption.length)
            .trim();
    }

    if (!newText) {
        previousCaption = currentCaption;
        return;
    }

    console.log("[NEW TEXT]", newText);

    sendToBackend(newText);

    previousCaption = currentCaption;
}


function sendToBackend(text, speaker) {

    console.log("[SEND]", speaker, text);

    chrome.runtime.sendMessage({
        type: "transcript",
        speaker: speaker,
        text: text,
        timestamp: Date.now()
    });
}


const observer = new MutationObserver(() => {
    readCaption();
});


observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true
});

/*
 * Call-end detection:
 * 1. Primary: user clicks the leave button
 *    (matched by aria-label, not class names).
 * 2. Confirmation: the caption UI must actually be
 *    torn down within a short window afterwards.
 * 3. Fallback: beforeunload (page close/reload).
 */

const LEAVE_LABEL_PATTERN =
    /^(leave|end)\s+(the\s+)?(call|meeting)$/;

let endConfirmTimer = null;

document.addEventListener(
    "click",
    (event) => {
        if (!meetCallDetected || sessionEnded) {
            return;
        }

        const target = event.target;
        const button = (
            target && typeof target.closest === "function"
        )
            ? target.closest("button[aria-label]")
            : null;

        if (!button) {
            return;
        }

        const label = (
            button.getAttribute("aria-label") || ""
        ).trim().toLowerCase();

        if (!LEAVE_LABEL_PATTERN.test(label)) {
            return;
        }

        console.log("[MEET] Leave button clicked, confirming...");

        clearTimeout(endConfirmTimer);

        endConfirmTimer = setTimeout(() => {
            const captionsGone = document.querySelectorAll(
                CAPTION_SELECTOR
            ).length === 0;

            if (captionsGone) {
                endSession();
            } else {
                console.log(
                    "[MEET] Call UI still active after leave click, ignoring."
                );
            }
        }, 1500);
    },
    true
);

// Sales AI Widget 

// ==================================================
// Sales AI Widget
// ==================================================

let widgetMinimized = false;


// ==================================================
// Create Widget
// ==================================================

function createWidget() {

    if (
        document.getElementById(
            "sales-ai-widget"
        )
    ) {
        return;
    }


    const widget =
        document.createElement("div");


    widget.id =
        "sales-ai-widget";


    widget.innerHTML = `

        <div class="sales-ai-header">

            <div class="sales-ai-heading">

                <div class="sales-ai-title">
                    ✦ Sales AI
                </div>

                <div class="sales-ai-status">

                    <span
                        class="sales-ai-dot"
                    ></span>

                    Live

                </div>

            </div>


            <button
                id="sales-ai-toggle"
                class="sales-ai-toggle"
                title="Minimize"
            >
                −
            </button>

        </div>


        <div
            id="sales-ai-content"
            class="sales-ai-content"
        >

            <!-- ======================================
                 CURRENT SUMMARY
                 ====================================== -->

            <div class="sales-ai-label">
                CURRENT SUMMARY
            </div>

            <div
                id="sales-ai-current-summary"
                class="sales-ai-current-summary"
            >
                Waiting for conversation...
            </div>


            <!-- ======================================
                 HISTORY
                 ====================================== -->

            <div class="sales-ai-history-label">
                HISTORY
            </div>

            <div
                id="sales-ai-timeline"
                class="sales-ai-timeline"
            >

                <div class="sales-ai-empty">
                    No snapshots yet.
                </div>

            </div>

        </div>


        <!-- ======================================
             SNAPSHOT CONTROLS
             ====================================== -->

        <div class="snapshot-controls">

            <span>
                Snapshot:
            </span>

            <button
                data-minutes="1"
            >
                1m
            </button>

            <button
                data-minutes="5"
                class="active"
            >
                5m
            </button>

            <button
                data-minutes="10"
            >
                10m
            </button>

        </div>

    `;


    document.body.appendChild(widget);


    // ==================================================
    // Styles
    // ==================================================

    const style = document.createElement("style");


    style.textContent = `

        #sales-ai-widget {

            position: fixed;

            top: 20px;
            right: 20px;

            width: 25vw;

            min-width: 300px;
            max-width: 430px;

            height: 55vh;
            max-height: 650px;

            background: #111318;

            color: #f5f5f5;

            border:
                1px solid #2d313a;

            border-radius: 14px;

            box-shadow:
                0 10px 35px
                rgba(0, 0, 0, 0.40);

            font-family:
                Inter,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;

            z-index: 2147483647;

            overflow: hidden;

            display: flex;

            flex-direction: column;

            transition:
                width 0.15s ease,
                height 0.15s ease;

        }


        /* ==========================================
           HEADER
           ========================================== */

        .sales-ai-header {

            min-height: 62px;

            padding:
                12px
                14px
                12px
                18px;

            border-bottom:
                1px solid #2d313a;

            display: flex;

            align-items: center;

            justify-content:
                space-between;

        }


        .sales-ai-heading {

            min-width: 0;

        }


        .sales-ai-title {

            font-size: 15px;

            font-weight: 600;

        }


        .sales-ai-status {

            margin-top: 4px;

            display: flex;

            align-items: center;

            gap: 6px;

            font-size: 11px;

            color: #8f96a3;

        }


        .sales-ai-dot {

            width: 7px;

            height: 7px;

            border-radius: 50%;

            background: #4ade80;

            display: inline-block;

        }


        .sales-ai-toggle {

            width: 30px;

            height: 30px;

            border: none;

            border-radius: 7px;

            background:
                transparent;

            color: #9ca3af;

            font-size: 20px;

            cursor: pointer;

        }


        .sales-ai-toggle:hover {

            background: #242832;

            color: white;

        }


        /* ==========================================
           CONTENT
           ========================================== */

        .sales-ai-content {

            flex: 1;

            min-height: 0;

            display: flex;

            flex-direction: column;

            padding: 16px;

            overflow: hidden;

        }


        .sales-ai-label,
        .sales-ai-history-label {

            flex-shrink: 0;

            font-size: 10px;

            font-weight: 600;

            letter-spacing: 0.08em;

            color: #8f96a3;

        }


        .sales-ai-label {

            margin-bottom: 10px;

        }


        /* ==========================================
           CURRENT SUMMARY
           ========================================== */

        .sales-ai-current-summary {

            flex-shrink: 0;

            max-height: 150px;

            overflow-y: auto;

            padding-bottom: 16px;

            font-size: 13px;

            line-height: 1.55;

            color: #e5e7eb;

            border-bottom:
                1px solid #252932;

        }


        /* ==========================================
           HISTORY
           ========================================== */

        .sales-ai-history-label {

            margin-top: 14px;

            margin-bottom: 10px;

        }


        .sales-ai-timeline {

            flex: 1;

            min-height: 0;

            overflow-y: auto;

            padding-right: 6px;

        }


        .sales-ai-timeline::-webkit-scrollbar {

            width: 5px;

        }


        .sales-ai-timeline::-webkit-scrollbar-thumb {

            background: #363b45;

            border-radius: 10px;

        }


        .sales-ai-entry {

            padding: 12px 0;

            border-bottom:
                1px solid #252932;

        }


        .sales-ai-entry:first-child {

            padding-top: 0;

        }


        .sales-ai-time {

            font-size: 11px;

            font-weight: 600;

            color: #7f8795;

            margin-bottom: 6px;

        }


        .sales-ai-entry-text {

            font-size: 13px;

            line-height: 1.55;

            color: #e5e7eb;

        }


        .sales-ai-empty {

            font-size: 13px;

            line-height: 1.5;

            color: #7f8795;

        }


        /* ==========================================
           SNAPSHOT CONTROLS
           ========================================== */

        .snapshot-controls {

            flex-shrink: 0;

            display: flex;

            align-items: center;

            gap: 6px;

            padding:
                10px 14px;

            border-top:
                1px solid #2d313a;

            font-size: 11px;

            color: #8f96a3;

        }


        .snapshot-controls button {

            border: 1px solid #343945;

            background: #1a1d24;

            color: #9ca3af;

            border-radius: 6px;

            padding:
                4px 8px;

            font-size: 11px;

            cursor: pointer;

        }


        .snapshot-controls button:hover {

            background: #242832;

            color: white;

        }


        .snapshot-controls button.active {

            background: #2d3340;

            color: white;

        }


        /* ==========================================
           MINIMIZED
           ========================================== */

        #sales-ai-widget.sales-ai-minimized {

            width: 125px;

            height: 48px;

            min-width: 125px;

            max-width: 125px;

            border-radius: 10px;

        }


        #sales-ai-widget.sales-ai-minimized
        .sales-ai-header {

            min-height: 48px;

            padding:
                8px 10px;

        }


        #sales-ai-widget.sales-ai-minimized
        .sales-ai-status {

            display: none;

        }


        #sales-ai-widget.sales-ai-minimized
        .sales-ai-toggle {

            font-size: 18px;

        }


        #sales-ai-widget.sales-ai-minimized
        .sales-ai-content,
        #sales-ai-widget.sales-ai-minimized
        .snapshot-controls {

            display: none;

        }

    `;


    document.head.appendChild(style);

// Minimize Expand
    const toggle = document.getElementById("sales-ai-toggle");
    toggle.addEventListener("click", () => {
            widgetMinimized = !widgetMinimized
            if (widgetMinimized) {
                widget.classList.add("sales-ai-minimized");
                toggle.textContent = "+";
                toggle.title = "Expand";
            } else {
                widget.classList.remove("sales-ai-minimized");
                toggle.textContent = "−";
                toggle.title = "Minimize";
            }
        }
    );


    // Snapshot buttons
    document.querySelectorAll(".snapshot-controls button")
        .forEach(button => {
            button.addEventListener("click", () => {
                    const minutes = Number(button.dataset.minutes);

                    if (button.classList.contains("active")) {
                        return;
                    }

                    document.querySelectorAll(".snapshot-controls button")
                        .forEach(
                            otherButton => {
                                otherButton.classList.remove("active");
                            }
                        );
                    button.classList.add("active");


                    // Tell background.js
                    chrome.runtime.sendMessage({
                        type:
                            "snapshot_interval",
                        minutes:
                            minutes
                    });

                    console.log(
                        "[SNAPSHOT INTERVAL]",
                        minutes,
                        "minutes"
                    );
                }
            );

        });

}


// Format a meeting-relative millisecond offset as MM:SS
function formatMeetingTime(relativeMs) {
    const totalSeconds = Math.max(
        0,
        Math.floor((relativeMs || 0) / 1000)
    );

    const minutes = Math.floor(totalSeconds / 60);

    const seconds = totalSeconds % 60;

    return (
        `${String(minutes).padStart(2, "0")}:` +
        `${String(seconds).padStart(2, "0")}`
    );
}

// Update Current Summary
function updateCurrentSummary(summary) {
    const element = document.getElementById("sales-ai-current-summary");
    if (!element) {
        return;
    }
    element.textContent = summary || "Waiting for conversation...";
}

// Add History Snapshot 

function addSnapshotToHistory(summary, timestamp) {
    const timeline = document.getElementById("sales-ai-timeline");
    if (!timeline) {
        return;
    }
    const empty = timeline.querySelector(".sales-ai-empty");

    if (empty) {
        empty.remove();
    }
    const entry = document.createElement("div");
    entry.className = "sales-ai-entry";
    const time = document.createElement("div");

    time.className = "sales-ai-time";
    time.textContent = formatMeetingTime(timestamp);
    const text = document.createElement("div");
    text.className = "sales-ai-entry-text";
    text.textContent = summary;
    entry.appendChild(time);
    entry.appendChild(text);
    // Newest snapshot at the top
    timeline.prepend(entry);
}

createWidget();


// Backend Messages

chrome.runtime.onMessage.addListener(
    (message) => {

        // Content summary

        if (message.type === "summary") {
            console.log("[AI CURRENT SUMMARY]", message.summary);

            updateCurrentSummary(message.summary);
        }

        // snapshot

        if (message.type === "snapshot") {
            console.log("[AI SNAPSHOT]", message.summary);

            // message.timestamp is meeting-relative ms (backend)
            addSnapshotToHistory(
                message.summary,
                message.timestamp
            );
        }
    }
);