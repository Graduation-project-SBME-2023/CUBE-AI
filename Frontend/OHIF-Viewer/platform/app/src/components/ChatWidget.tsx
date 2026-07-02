import React, { useEffect, useRef, useState } from "react";
import websocketService from "../utils/WebsocketService";
import { getAgentsBaseUrl, getMonaiBaseUrl } from "../utils/runtimeUrls";
type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: number;
};

type ChatSession = {
  chat_id: string;
  title: string;
  messages: Message[];
  summary?: string;
  last_activity?: number;
};

const makeId = (prefix = "id") => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;

const CustomChatWidget: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const activeSession = sessions.find((s) => s.chat_id === activeSessionId) || null;

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);


  const messagesWithSummary = activeSession ? activeSession.summary
    ? [
      { id: "summary", role: "assistant", text: activeSession.summary, ts: 0 },
      ...activeSession.messages,
    ]
    : activeSession.messages
    : [];


  // --- Backend calls ---
  const fetchChatSessions = async () => {
    try {
      const r = await fetch(`${getMonaiBaseUrl()}/chat_sessions/`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const mapped: ChatSession[] = (data || []).map((s: any) => ({
        chat_id: String(s.chat_id),
        title: s.title || `Chat ${s.chat_id}`,
        messages: [],
        summary: s.summary,
        last_activity: s.last_activity ? new Date(s.last_activity).getTime() : 0,
      }));
      setSessions(mapped);
      if (mapped.length && !activeSessionId) setActiveSessionId(mapped[0].chat_id);
    } catch (err) {
      console.warn("Failed to fetch sessions from backend", err);
    }
  };

  const fetchMessagesForSession = async (chat_id: string) => {
    try {
      const res = await fetch(`${getMonaiBaseUrl()}/chat_messages/${chat_id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const msgs: any[] = await res.json();
      setSessions((prev) =>
        prev.map((s) =>
          s.chat_id === chat_id
            ? {
              ...s,
              messages: msgs.map((m) => ({
                id: `db_${m.message_id}`,
                role: m.role,
                text: m.text,
                ts: new Date(m.timestamp).getTime(),
              })),
            }
            : s
        )
      );
    } catch (err) {
      console.warn("Failed to fetch messages for session", err);
    }
  };

  const createNewSession = async (title?: string) => {
    try {
      const payload = { title: title || `chat ${sessions.length + 1}` };
      const r = await fetch(`${getMonaiBaseUrl()}/chat_sessions/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      console.log(data);

      const newSession: ChatSession = {
        chat_id: String(data.chat_id),
        title: data.title || payload.title,
        messages: [
          { id: makeId("sys"), role: "system", text: "Hello Doctor! I am your radiologist assistant.", ts: Date.now() },
        ],
        summary: data.summary,
        last_activity: Date.now(),
      };

      // Save initial system message to backend
      await fetch(`${getMonaiBaseUrl()}/chat_messages/${newSession.chat_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "system", text: newSession.messages[0].text }),
      });

      setSessions((s) => [newSession, ...s]);
      setActiveSessionId(newSession.chat_id);
      setDrawerOpen(false);
      setOpen(true);
    } catch (err) {
      console.warn("Failed to create session", err);
    }
  };

  const updateSessionLastActivity = async (chat_id: string) => {
    try {
      await fetch(`${getMonaiBaseUrl()}/chat_sessions/${chat_id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ last_activity: new Date().toISOString() }),
      });
      setSessions((prev) =>
        prev.map((s) => (s.chat_id === chat_id ? { ...s, last_activity: Date.now() } : s))
      );
    } catch (err) {
      console.warn("Failed to update last_activity", err);
    }
  };

  const deleteSession = async (chat_id: string) => {
    try {
      await fetch(`${getMonaiBaseUrl()}/chat_sessions/${chat_id}`, { method: "DELETE" });
    } catch (err) {
      console.warn("Failed to delete session", err);
    }
    setSessions((s) => s.filter((x) => x.chat_id !== chat_id));
    if (activeSessionId === chat_id) {
      const remaining = sessions.filter((x) => x.chat_id !== chat_id);
      setActiveSessionId(remaining.length ? remaining[0].chat_id : null);
    }
  };

  const mockAgentResponse = async (text: string) => {
    try {
      const res = await fetch(`${getAgentsBaseUrl()}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: text }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data.response || "No response from agent.";
    } catch (err) {
      console.warn("Agent fetch failed", err);
      await new Promise((r) => setTimeout(r, 600));
      return "⚠️ Could not reach the radiologist assistant server. Offline reply.";
    }
  };

  const sendMessage = async (text: string) => {
    if (!text.trim()) return;
    if (!activeSessionId) await createNewSession();
    const sessionId = activeSessionId ?? sessions[0]?.chat_id;
    if (!sessionId) return;

    const userMsg: Message = { id: makeId("u"), role: "user", text, ts: Date.now() };
    console.log("Sending message to session", sessionId);
    console.log(userMsg);
    // Add to local state
    setSessions((prev) =>
      prev.map((s) =>
        s.chat_id === sessionId ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() } : s
      )
    );

    // Save to backend
    await fetch(`${getMonaiBaseUrl()}/chat_messages/${sessionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: "user", text }),
    });

    // Send via WebSocket
    websocketService.sendMessage({ type: "user", payload: text });

    setInput("");
    setIsTyping(true);
  };

  const openSession = async (chat_id: string) => {
    setActiveSessionId(chat_id);
    setOpen(true);
    setDrawerOpen(false);
    await fetchMessagesForSession(chat_id);
  };

  useEffect(() => {
    fetchChatSessions();
    websocketService.connect();

  }, []);


  const sortedSessions = [...sessions].sort((a, b) => (b.last_activity || 0) - (a.last_activity || 0));

  useEffect(() => {

    // 2. Handle incoming WebSocket messages
    const handleWS = async (data: any) => {
      const { type, payload } = data;

      // Filter types (Add 'hypothesis' or 'plan' here if you want to display them)
      // currently only handling 'msg' based on your logic
      if (type !== "msg") return;

      console.log("[WS] received assistant:", payload);

      // USE THE REF HERE: This ensures we get the current ID without restarting the effect
      const currentSessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;

      if (!currentSessionId) return;

      // Safely handle text vs object payloads
      const textContent = typeof payload === "string"
        ? payload
        : (payload.text || JSON.stringify(payload));

      const assistantMsg: Message = {
        id: makeId("a"),
        role: "assistant",
        text: textContent,
        ts: payload.ts || Date.now(),
      };

      // 3. Add assistant message using Functional State Update
      // We don't need 'sessions' as a dependency because we use 'prev'
      setSessions((prev) =>
        prev.map((s) =>
          s.chat_id === currentSessionId
            ? {
              ...s,
              messages: [...s.messages, assistantMsg],
              last_activity: Date.now()
            }
            : s
        )
      );

      // 4. Save in backend
      try {
        await fetch(`${getMonaiBaseUrl()}/chat_messages/${currentSessionId}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "assistant", text: assistantMsg.text }),
        });
      } catch (err) {
        console.warn("Failed to save assistant message", err);
      }

      setIsTyping(false);

      // Note: We avoid calling updateSessionLastActivity here if it triggers 
      // a fetch that updates 'sessions' again from the outside, 
      // but the local setSessions above handles the UI update.
    };

    // Subscribe
    const unsubscribe = websocketService.subscribe(handleWS);

    // Cleanup on unmount only
    return () => {
      unsubscribe();
    };

    // DEPENDENCY ARRAY IS EMPTY: This effect runs once on mount, never again.
  }, []);
  // --- Scroll to bottom ---
  useEffect(() => {
    if (!listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [sessions, activeSessionId, open, drawerOpen]);


  return (
    <div className="fixed bottom-6 right-6 z-50">
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Open chat"
          className="flex items-center justify-center w-14 h-14 rounded-full shadow-lg bg-white ring-1 ring-gray-200"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-7 h-7 text-sky-600" viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <path strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 1 0-3.95 7.06L21 21l-1.06-2.95A8.96 8.96 0 0 0 21 12z" />
          </svg>
        </button>
      )}

      {open && (
        <div className="w-full max-w-[800px] h-[560px] bg-white shadow-2xl rounded-xl relative flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b">
            <div className="flex items-center gap-3">
              <img src="/assets/robot.png" alt="bot" className="w-10 h-10" />
              <div>
                <div className="font-semibold text-sm">AI Assistant</div>
                <div className="text-xs text-gray-500">Hello Doctor, how can I help you today?</div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button title="History" onClick={() => setDrawerOpen((v) => !v)} className="px-2 py-1 rounded-md hover:bg-gray-100">📜</button>
              <button title="New" onClick={() => createNewSession()} className="px-2 py-1 rounded-md hover:bg-gray-100">➕</button>
              <button title="Close" onClick={() => setOpen(false)} className="ml-1 text-lg font-bold px-2 py-1 rounded-md hover:bg-gray-100">×</button>
            </div>
          </div>

          <div className="flex-1 flex min-h-0">
            {/* Drawer */}
            <div className={`transition-all duration-200 border-r ${drawerOpen ? "w-56" : "w-0 overflow-hidden"}`}>
              <div className="h-full flex flex-col">
                <div className="px-3 py-3 border-b flex items-center justify-between">
                  <div className="text-sm font-medium">History</div>
                  <button onClick={() => setDrawerOpen(false)} className="text-sm">✕</button>
                </div>
                <div className="p-3 overflow-auto flex-1">
                  {sortedSessions.length === 0 ? (
                    <div className="text-xs text-gray-500">No conversations yet.</div>
                  ) : (
                    <ul className="space-y-2">
                      {sortedSessions.map((s) => (
                        <li
                          key={s.chat_id}
                          className={`p-2 rounded-lg flex items-center justify-between cursor-pointer
                                ${s.chat_id === activeSessionId ? "bg-gray-400 shadow-md" : "hover:bg-gray-50"}`}
                        >

                          {/* LEFT SIDE — Text */}
                          <div
                            onClick={() => openSession(s.chat_id)}
                            className="flex-1"
                          >
                            <div className="text-sm font-medium truncate w-[150px]">{s.title}</div>
                            <div className={`text-xs truncate w-[150px] ${s.chat_id === activeSessionId ? "text-black" : "text-gray-500"}`}>
                              {s.summary ?? s.messages[s.messages.length - 1]?.text?.slice(0, 60) ?? ""}
                            </div>
                          </div>

                          {/* RIGHT SIDE — Delete button */}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDeleteId(s.chat_id);
                            }}
                            className="ml-3 flex-shrink-0 p-1 rounded-md hover:bg-red-100 group"
                            title="Delete"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg"
                              className="w-4 h-4 text-red-500 group-hover:text-red-700"
                              viewBox="0 0 24 24" fill="none" stroke="currentColor"
                            >
                              <path strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                                d="M3 6h18M9 6v12m6-12v12M4 6l1 14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2l1-14M10 6a2 2 0 0 1 4 0" />
                            </svg>
                          </button>
                        </li>



                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>

            {/* Chat messages */}
            <div className="flex-1 flex flex-col min-h-0">
              <div ref={listRef} className="flex-1 p-4 overflow-y-auto space-y-3 bg-[linear-gradient(180deg,#ffffff,#f8fafc)]">
                {messagesWithSummary.length > 0 ? (
                  messagesWithSummary.map((m) => (
                    <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div className="overflow-x-auto max-w-full">
                        <div className={`${m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-200 text-gray-900"} p-3 rounded-lg shadow break-words whitespace-pre-wrap inline-block`} style={{ overflowWrap: "anywhere", minWidth: "fit-content" }}>
                          {m.text}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-center text-sm text-gray-500 mt-12">
                    No conversation selected. Start a new chat or open history.
                  </div>
                )}

                {isTyping && (
                  <div className="flex justify-start">
                    <div className="bg-gray-100 p-2 rounded-lg text-xs text-gray-600">Typing...</div>
                  </div>
                )}
              </div>

              <div className="px-3 py-3 border-t">
                <form onSubmit={(e) => { e.preventDefault(); sendMessage(input); }} className="flex items-center gap-2">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Write a message..."
                    className="flex-1 rounded-lg px-3 py-2 border focus:outline-none focus:ring-1 focus:ring-sky-300"
                  />
                  <button type="submit" className="p-2 rounded-lg bg-sky-600 text-white flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#1d4ed8" className="w-6 h-6">
                      <path d="M2 21l21-9L2 3v7l15 2-15 2v7z" />
                    </svg>
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>
      )}
      {/* Delete Confirmation Modal */}
      {confirmDeleteId !== null && (
        <div className="absolute inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white p-5 rounded-lg shadow-lg w-64">
            <div className="font-semibold text-center mb-3">
              Delete this chat?
            </div>
            <div className="text-xs text-gray-600 text-center mb-4">
              This action cannot be undone.
            </div>

            <div className="flex justify-between">
              <button
                onClick={() => setConfirmDeleteId(null)}
                className="px-3 py-1 rounded bg-gray-200 hover:bg-gray-300 text-sm"
              >
                Cancel
              </button>

              <button
                onClick={() => {
                  deleteSession(confirmDeleteId);
                  setConfirmDeleteId(null);
                }}
                className="px-3 py-1 rounded bg-red-600 hover:bg-red-700 text-white text-sm"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default CustomChatWidget;
