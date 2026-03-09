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

document.addEventListener("click", (e) => {
    const targetId = e.target.id || e.target.tagName.toLowerCase();
    sendBrowserEvent("click", targetId);
});

document.addEventListener("focusin", (e) => {
    const targetId = e.target.id || e.target.tagName.toLowerCase();
    sendBrowserEvent("input_focus", targetId);
});

const nudge = document.getElementById("nudge");
const chatWidget = document.getElementById("chat-widget");
const chatBody = document.getElementById("chat-body");
const sendChat = document.getElementById("send-chat");
const chatInput = document.getElementById("chat-input");
const closeChat = document.getElementById("close-chat");

function appendMessage(text, sender = "assistant") {
    const div = document.createElement("div");
    div.className = `message ${sender}`;
    div.textContent = `${sender}: ${text}`;
    chatBody.appendChild(div);
    chatBody.scrollTop = chatBody.scrollHeight;
}

async function pollUiState() {
    const res = await fetch("/api/ui_state");
    const data = await res.json();

    if (data.nudge && !data.assistant_open) {
        nudge.classList.remove("hidden");
    } else {
        nudge.classList.add("hidden");
    }

    if (data.assistant_open) {
        chatWidget.classList.remove("hidden");

        if (!chatBody.dataset.lastMessage || chatBody.dataset.lastMessage !== data.assistant_message) {
            appendMessage(data.assistant_message, "assistant");
            chatBody.dataset.lastMessage = data.assistant_message;
        }
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
    appendMessage(data.assistant_message, "assistant");
    chatBody.dataset.lastMessage = data.assistant_message;
});

closeChat.addEventListener("click", async () => {
    await fetch("/api/close_chat", { method: "POST" });
    chatWidget.classList.add("hidden");
});

document.querySelector(".chat-header").addEventListener("click", () => {
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

setInterval(pollUiState, 1500);