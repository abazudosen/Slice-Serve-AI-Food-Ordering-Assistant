let sessionId = null;
let requestInProgress = false;
let requestGeneration = 0;
let activeController = null;

const messages = document.getElementById("messages");
const stateView = document.getElementById("state");
const traceView = document.getElementById("trace");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("message");
const sendButton = composer.querySelector("button");
const flowSelect = document.getElementById("flow");
const chatTitle = document.getElementById("chat-title");
const chatSubtitle = document.getElementById("chat-subtitle");
const savedSessions = document.getElementById("saved-sessions");
const loadSessionButton = document.getElementById("load-session");
const deleteSessionButton = document.getElementById("delete-session");
const stopTurnButton = document.getElementById("stop-turn");
const sessionsStatus = document.getElementById("sessions-status");
const newOrderButton = document.getElementById("new-order");
const errorBanner = document.getElementById("error-banner");
const orderDetails = document.getElementById("order-details");
const orderProgress = document.getElementById("order-progress");
const progressTrack = document.querySelector(".progress-track");
const progressLabel = document.getElementById("progress-label");
const orderStatus = document.getElementById("order-status");
const connectionIndicator = document.getElementById("connection-indicator");
const connectionLabel = document.getElementById("connection-label");
const promptButtons = [...document.querySelectorAll("[data-prompt]")];

const orderFields = {
  pizza: [
    ["size", "Size"],
    ["crust", "Crust"],
    ["toppings", "Toppings"],
    ["address", "Delivery"],
    ["payment_method", "Payment"],
  ],
  bagel: [
    ["bread_type", "Bread"],
    ["filling", "Filling"],
    ["extras", "Extras"],
    ["address", "Delivery"],
    ["payment_method", "Payment"],
  ],
};

function showSessionsError(error) {
  sessionsStatus.textContent = `Could not load saved orders: ${error.message}`;
}

function displayError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.textContent = "";
  errorBanner.hidden = true;
}

async function refreshSavedSessions() {
  const selectedSessionId = savedSessions.value;
  const response = await fetch(`/api/sessions?flow=${encodeURIComponent(flowSelect.value)}`);
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }

  const sessions = await response.json();
  savedSessions.replaceChildren(new Option("Select a saved order", ""));
  for (const savedSession of sessions) {
    const updatedAt = new Date(`${savedSession.updated_at}Z`).toLocaleString();
    savedSessions.add(new Option(
      `${savedSession.summary} · ${updatedAt}`,
      savedSession.session_id
    ));
  }
  if (sessions.some((savedSession) => savedSession.session_id === selectedSessionId)) {
    savedSessions.value = selectedSessionId;
  }
  sessionsStatus.textContent = sessions.length ? "" : "No saved orders for this menu yet.";
}

async function checkConnection() {
  try {
    const response = await fetch("/api/sessions", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`);
    }
    connectionIndicator.classList.add("online");
    connectionIndicator.classList.remove("offline");
    connectionLabel.textContent = "App connected";
  } catch {
    connectionIndicator.classList.add("offline");
    connectionIndicator.classList.remove("online");
    connectionLabel.textContent = "App unavailable";
  }
}

function addBubble(text, role) {
  const row = document.createElement("div");
  row.className = `message-row ${role === "user" ? "user-row" : "agent"}`;

  if (role !== "user") {
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "✦";
    row.appendChild(avatar);
  }

  const message = document.createElement("div");
  const metadata = document.createElement("div");
  metadata.className = "message-meta";
  metadata.textContent = role === "user" ? "You" : "Order assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  message.append(metadata, bubble);
  row.appendChild(message);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function addPendingBubble() {
  const row = document.createElement("div");
  row.className = "message-row agent pending";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = "✦";

  const message = document.createElement("div");
  const metadata = document.createElement("div");
  metadata.className = "message-meta";
  metadata.textContent = "Order assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = "Putting that together… ";
  const dots = document.createElement("span");
  dots.className = "typing-dots";
  dots.setAttribute("aria-label", "Working");
  for (let index = 0; index < 3; index += 1) {
    dots.appendChild(document.createElement("span"));
  }
  bubble.appendChild(dots);
  message.append(metadata, bubble);
  row.append(avatar, message);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function renderOrder(state, flow) {
  const fields = orderFields[flow];
  const completeCount = fields.filter(([key]) => {
    const value = state[key];
    return Array.isArray(value) ? value.length > 0 : Boolean(value);
  }).length;

  orderDetails.replaceChildren();
  for (const [key, label] of fields) {
    const value = state[key];
    const row = document.createElement("div");
    row.className = "detail-row";
    const labelElement = document.createElement("span");
    labelElement.className = "detail-label";
    labelElement.textContent = label;
    const valueElement = document.createElement("span");
    valueElement.className = `detail-value${value ? "" : " empty"}`;
    valueElement.textContent = Array.isArray(value) && value.length
      ? value.join(", ")
      : (value || "Not set");
    row.append(labelElement, valueElement);
    orderDetails.appendChild(row);
  }

  if (state.allergies?.length) {
    const allergyRow = document.createElement("div");
    allergyRow.className = "detail-row";
    const allergyLabel = document.createElement("span");
    allergyLabel.className = "detail-label";
    allergyLabel.textContent = "Allergies";
    const allergyValue = document.createElement("span");
    allergyValue.className = "detail-value";
    allergyValue.textContent = state.allergies.join(", ");
    allergyRow.append(allergyLabel, allergyValue);
    orderDetails.appendChild(allergyRow);
  }

  const percentage = Math.round((completeCount / fields.length) * 100);
  orderProgress.style.width = `${percentage}%`;
  progressTrack.setAttribute("aria-valuenow", String(completeCount));
  progressLabel.innerHTML = `<strong>${completeCount}</strong> / ${fields.length}`;
  if (state.order_id) {
    orderStatus.textContent = "ORDER PLACED";
    orderStatus.classList.add("placed");
  } else {
    orderStatus.textContent = "IN PROGRESS";
    orderStatus.classList.remove("placed");
  }
  stateView.textContent = JSON.stringify(state, null, 2);
}

function updateFlowContent() {
  const flow = flowSelect.value;
  const isBagel = flow === "bagel";
  chatTitle.textContent = isBagel ? "Bagel order" : "Pizza order";
  chatSubtitle.textContent = isBagel
    ? "Choose your bagel and we’ll take it one step at a time."
    : "Tell me what sounds good. We can work out the details together.";
  messageInput.placeholder = isBagel
    ? "Try: everything bagel with cream cheese..."
    : "Try: medium thin crust with olives...";

  const prompts = isBagel
    ? [
      ["Everything bagel with cream cheese", "Everything bagel"],
      ["Add onion and capers", "Add extras"],
      ["Pay by card", "Payment"],
    ]
    : [
      ["Medium thin crust with olives", "Medium thin crust"],
      ["Address is 10 Main St, NY", "Add delivery address"],
      ["Pay by card", "Payment"],
    ];
  promptButtons.forEach((button, index) => {
    button.dataset.prompt = prompts[index][0];
    button.textContent = prompts[index][1];
  });
}

function startNewOrder() {
  sessionId = crypto.randomUUID();
  messages.replaceChildren();
  stateView.textContent = "{}";
  traceView.textContent = "[]";
  clearError();
  updateFlowContent();
  addBubble(
    flowSelect.value === "bagel"
      ? "Hi! I can help with a bagel order. Tell me the bread, filling, and any extras. You can share details all at once or one at a time."
      : "Hi! I can help with a pizza order. Tell me the size, crust, and toppings. You can share details all at once or one at a time.",
    "agent"
  );
  renderOrder({}, flowSelect.value);
  refreshSavedSessions().catch(showSessionsError);
  messageInput.focus();
}

async function sendMessage(text) {
  const generation = ++requestGeneration;
  activeController = new AbortController();
  const pendingBubble = addPendingBubble();
  requestInProgress = true;
  sendButton.disabled = true;
  messageInput.disabled = true;
  newOrderButton.disabled = true;
  flowSelect.disabled = true;
  stopTurnButton.hidden = false;
  stopTurnButton.disabled = false;
  clearError();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text, flow: flowSelect.value }),
      signal: activeController.signal,
    });
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const errorBody = await response.json();
        if (typeof errorBody.detail === "string") {
          detail = errorBody.detail;
        }
      } catch {
        // Keep the HTTP status as the user-facing error when no JSON detail is available.
      }
      throw new Error(detail);
    }
    const data = await response.json();
    sessionId = data.session_id;
    renderOrder(data.state, data.flow);
    traceView.textContent = JSON.stringify(data.trace, null, 2);
    addBubble(data.reply, "agent");
    refreshSavedSessions().catch(showSessionsError);
  } finally {
    pendingBubble.remove();
    if (generation === requestGeneration) {
      requestInProgress = false;
      activeController = null;
      stopTurnButton.hidden = true;
      sendButton.disabled = false;
      messageInput.disabled = false;
      newOrderButton.disabled = false;
      flowSelect.disabled = false;
      messageInput.focus();
    }
  }
}

flowSelect.addEventListener("change", startNewOrder);
newOrderButton.addEventListener("click", startNewOrder);

stopTurnButton.addEventListener("click", async () => {
  if (!requestInProgress || !sessionId) return;
  stopTurnButton.disabled = true;
  let cancellationConfirmed = false;
  let cancellationRequestFailed = false;
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(sessionId)}/cancel`, { method: "POST" });
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    const result = await response.json();
    cancellationConfirmed = result.cancelled === true;
  } catch {
    cancellationRequestFailed = true;
  }
  if (!cancellationConfirmed && !cancellationRequestFailed) {
    displayError("That turn has already finished; its response is being returned.");
    return;
  }
  requestGeneration += 1;
  activeController?.abort();
  activeController = null;
  requestInProgress = false;
  stopTurnButton.hidden = true;
  sendButton.disabled = false;
  messageInput.disabled = false;
  newOrderButton.disabled = false;
  flowSelect.disabled = false;
  if (cancellationRequestFailed) {
    displayError("Could not confirm the stop request. Sending a new message will still supersede an active turn.");
  }
  addBubble("Stopped that turn. Tell me your updated intent when you're ready.", "agent");
  messageInput.focus();
});

deleteSessionButton.addEventListener("click", async () => {
  const selectedSessionId = savedSessions.value;
  if (!selectedSessionId) {
    sessionsStatus.textContent = "Choose a saved order to delete.";
    return;
  }
  if (!window.confirm("Permanently delete this saved order and its debug trace?")) return;

  deleteSessionButton.disabled = true;
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(selectedSessionId)}`, {
      method: "DELETE",
    });
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    if (sessionId === selectedSessionId) startNewOrder();
    await refreshSavedSessions();
    sessionsStatus.textContent = "Saved order and trace deleted.";
  } catch (error) {
    displayError(`Could not delete this saved order. ${error.message}`);
  } finally {
    deleteSessionButton.disabled = false;
  }
});

loadSessionButton.addEventListener("click", async () => {
  const selectedSessionId = savedSessions.value;
  if (!selectedSessionId) {
    sessionsStatus.textContent = "Choose a saved order to resume.";
    return;
  }

  loadSessionButton.disabled = true;
  clearError();
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(selectedSessionId)}`);
    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`);
    }
    const data = await response.json();
    sessionId = data.session_id;
    flowSelect.value = data.flow;
    updateFlowContent();
    messages.replaceChildren();
    addBubble(`Welcome back. Your saved ${data.flow} order is ready to continue.`, "agent");
    renderOrder(data.state, data.flow);
    traceView.textContent = JSON.stringify(data.trace, null, 2);
    sessionsStatus.textContent = "Order resumed.";
  } catch (error) {
    showSessionsError(error);
    displayError(`Could not resume this order. ${error.message}`);
  } finally {
    loadSessionButton.disabled = false;
  }
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text || requestInProgress) return;

  addBubble(text, "user");
  messageInput.value = "";
  try {
    await sendMessage(text);
  } catch (error) {
    displayError(`I couldn't reach the order service. Your message wasn't saved. ${error.message}`);
  }
});

promptButtons.forEach((button) => {
  button.addEventListener("click", () => {
    messageInput.value = button.dataset.prompt || "";
    composer.requestSubmit();
  });
});

(async () => {
  updateFlowContent();
  startNewOrder();
  await Promise.all([
    refreshSavedSessions().catch(showSessionsError),
    checkConnection(),
  ]);
})();
