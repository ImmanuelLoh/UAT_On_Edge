function sendBrowserEvent(type, target = null) {
    fetch("/api/browser_event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            type: type,
            target: target
        })
    });
}

// document.addEventListener("click", (e) => {
//     const targetId = e.target.id || e.target.tagName.toLowerCase();
//     sendBrowserEvent("click", targetId);
// });

// document.addEventListener("focusin", (e) => {
//     const targetId = e.target.id || e.target.tagName.toLowerCase();
//     sendBrowserEvent("input_focus", targetId);
// });

const nudge = document.getElementById("nudge");
const chatWidget = document.getElementById("chat-widget");
const chatBody = document.getElementById("chat-body");
const sendChat = document.getElementById("send-chat");
const chatInput = document.getElementById("chat-input");
const closeChat = document.getElementById("close-chat");

let pollInFlight = false;

function appendMessage(text, sender = "assistant") {
    const cleanText = (text || "").trim();
    if (!cleanText) return;

    const div = document.createElement("div");
    div.className = `message ${sender}`;
    div.textContent = `${sender}: ${cleanText}`;
    chatBody.appendChild(div);
    chatBody.scrollTop = chatBody.scrollHeight;
}

async function pollUiState() {
    if (pollInFlight) return;
    pollInFlight = true;

    try {
        const res = await fetch("/api/ui_state");
        const data = await res.json();

        if (data.nudge && !data.assistant_open) {
            nudge.classList.remove("hidden");
        } else {
            nudge.classList.add("hidden");
        }

        if (data.assistant_open) {
            chatWidget.classList.remove("hidden");
        }

        const msg = (data.assistant_message || "").trim();

        if (data.assistant_open && msg && chatBody.dataset.lastMessage !== msg) {
            appendMessage(msg, "assistant");
            chatBody.dataset.lastMessage = msg;
        }
    } catch (err) {
        console.error("pollUiState failed:", err);
    } finally {
        pollInFlight = false;
    }
}

sendChat.addEventListener("click", async () => {
    const msg = chatInput.value.trim();
    if (!msg) return;

    appendMessage(msg, "user");
    chatInput.value = "";

    const res = await fetch("/api/chat_reply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg })
    });

    const data = await res.json();
    const reply = (data.assistant_message || "").trim();

    if (reply) {
        appendMessage(reply, "assistant");
        chatBody.dataset.lastMessage = reply;
    }
});

closeChat.addEventListener("click", async (e) => {
    e.stopPropagation();
    await fetch("/api/close_chat", { method: "POST" });
    chatWidget.classList.remove("hidden");
    chatWidget.classList.add("collapsed");
});

document.querySelector(".chat-header").addEventListener("click", () => {
    chatWidget.classList.remove("hidden");
    chatWidget.classList.remove("collapsed");
});

nudge.addEventListener("click", async () => {
    chatWidget.classList.remove("hidden");
    chatWidget.classList.remove("collapsed");
    nudge.classList.add("hidden");

    await fetch("/api/browser_event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            type: "manual_help_open",
            target: "nudge"
        })
    });
});

pollUiState();
setInterval(pollUiState, 1500);