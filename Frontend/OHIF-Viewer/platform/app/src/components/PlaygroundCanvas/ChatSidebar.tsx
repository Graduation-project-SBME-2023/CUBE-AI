import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, History, Plus, ChevronRight, Trash2, Send } from 'lucide-react';
import websocketService from '../../utils/WebsocketService'; // Adjust path as needed
import './ChatSidebar.css';

const makeId = (prefix = "id") => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;

const ChatSidebar = ({ isOpen, onClose, initialUserMessage }) => {
    const [sessions, setSessions] = useState([]);
    const [activeSessionId, setActiveSessionId] = useState(null);
    const [input, setInput] = useState("");
    const [isTyping, setIsTyping] = useState(false);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const messagesEndRef = useRef(null);
    const activeSessionIdRef = useRef(null);
    const activeSession = sessions.find((s) => s.chat_id === activeSessionId) || null;
    let messages = activeSession ? activeSession.messages : [];
    // If there are no messages and an initialUserMessage is provided, show it as the first user message
    if (messages.length === 0 && initialUserMessage) {
        messages = [{ id: makeId('init'), role: 'user', text: initialUserMessage, ts: Date.now() }];
    }
    useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);
    const fetchChatSessions = async () => {
        try {
            const r = await fetch("http://localhost:8000/chat_sessions/");
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            const mapped = (data || []).map((s) => ({
                chat_id: Number(s.chat_id),
                title: s.title || `Chat ${s.chat_id}`,
                messages: [],
                summary: s.summary,
                last_activity: s.last_activity ? new Date(s.last_activity).getTime() : 0,
            }));
            setSessions(mapped);
            if (mapped.length && !activeSessionId) setActiveSessionId(mapped[0].chat_id);
        } catch (err) {}
    };
    const fetchMessagesForSession = async (chat_id) => {
        try {
            const res = await fetch(`http://localhost:8000/chat_messages/${chat_id}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const msgs = await res.json();
            setSessions((prev) => prev.map((s) => s.chat_id === chat_id ? {
                ...s,
                messages: msgs.map((m) => ({
                    id: `db_${m.message_id}`,
                    role: m.role,
                    text: m.text,
                    ts: new Date(m.timestamp).getTime(),
                })),
            } : s));
        } catch (err) {}
    };
    const createNewSession = async () => {
        try {
            const payload = { title: `Chat ${sessions.length + 1}` };
            const r = await fetch("http://localhost:8000/chat_sessions/", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            const newSession = {
                chat_id: Number(data.chat_id),
                title: data.title || payload.title,
                messages: [{ id: makeId("sys"), role: "system", text: "Hello! I am your research assistant.", ts: Date.now() }],
                summary: data.summary,
                last_activity: Date.now(),
            };
            setSessions((s) => [newSession, ...s]);
            setActiveSessionId(newSession.chat_id);
            setDrawerOpen(false);
        } catch (err) {
            // Fallback: create a local-only session if DB fails
            const fallbackId = Date.now();
            const newSession = {
                chat_id: fallbackId,
                title: `Local Chat ${sessions.length + 1}`,
                messages: [{ id: makeId("sys"), role: "system", text: "Hello! (local session, DB unavailable)", ts: Date.now() }],
                summary: '',
                last_activity: Date.now(),
            };
            setSessions((s) => [newSession, ...s]);
            setActiveSessionId(newSession.chat_id);
            setDrawerOpen(false);
            alert("Warning: Could not create chat session in the database. Using a local-only session.\n\nError: " + err);
        }
    };
    const deleteSession = async (chat_id) => {
        try { await fetch(`http://localhost:8000/chat_sessions/${chat_id}`, { method: "DELETE" }); } catch (err) {}
        setSessions((s) => s.filter((x) => x.chat_id !== chat_id));
        if (activeSessionId === chat_id) setActiveSessionId(null);
    };
    const sendMessage = async () => {
        if (!input.trim()) return;
        let sessionId = activeSessionId;
        // If no session, create one and wait for state update
        if (!sessionId) {
            await createNewSession();
            sessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;
            if (!sessionId) return;
        }
        const userMsg = { id: makeId("u"), role: "user", text: input, ts: Date.now() };
        setSessions((prev) => prev.map((s) => s.chat_id === sessionId ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() } : s));
        setInput("");
        setIsTyping(true);
        let dbFailed = false;
        try {
            await fetch(`http://localhost:8000/chat_messages/${sessionId}`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: "user", text: userMsg.text }),
            });
        } catch(e) {
            dbFailed = true;
            alert("Warning: Could not save message to the database. This chat is now local-only.\n\nError: " + e);
        }
        // Debug log outgoing message
        console.log("[ChatSidebar] Sending WS message:", { type: "user", payload: userMsg.text });
        if (websocketService.isConnected()) {
            websocketService.sendMessage({ type: "user", payload: userMsg.text });
        } else {
            alert("WebSocket not connected. Message not sent to backend in real time.");
        }
    };
    // Only connect WebSocket once for the app session (not just when sidebar is open)
    useEffect(() => {
        websocketService.connect();
        fetchChatSessions();
        const handleWS = async (data) => {
            console.log("[ChatSidebar] WS received:", data);
            if (data.type !== "msg") return;
            const currentSessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;
            if (!currentSessionId) return;
            const textContent = typeof data.payload === "string" ? data.payload : (data.payload.text || JSON.stringify(data.payload));
            const assistantMsg = { id: makeId("a"), role: "assistant", text: textContent, ts: Date.now() };
            setSessions((prev) => prev.map((s) => s.chat_id === currentSessionId ? { ...s, messages: [...s.messages, assistantMsg], last_activity: Date.now() } : s));
            setIsTyping(false);
            try {
                await fetch(`http://localhost:8000/chat_messages/${currentSessionId}`, {
                    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: "assistant", text: assistantMsg.text }),
                });
            } catch (err) {}
        };
        const unsubscribe = websocketService.subscribe(handleWS);
        return () => unsubscribe();
    }, []);
    useEffect(() => {
        if (activeSessionId) fetchMessagesForSession(activeSessionId);
    }, [activeSessionId]);
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isTyping]);
    if (!isOpen) return null;
    return (
        <div className="w-chat-sidebar">
            <div className="w-chat-header">
                <div className="w-chat-header-title">
                    <MessageSquare size={16} /> Assistant
                </div>
                <div className="w-chat-header-actions">
                    <button onClick={() => setDrawerOpen(!drawerOpen)} title="History">
                        <History size={16} />
                    </button>
                    <button onClick={() => createNewSession()} title="New Chat">
                        <Plus size={16} />
                    </button>
                    <button onClick={onClose} style={{ marginLeft: 8 }}>
                        <ChevronRight size={18} />
                    </button>
                </div>
            </div>
            {drawerOpen && (
                <div className="w-chat-history-drawer">
                    <div className="w-chat-history-list">
                        <div className="w-chat-history-title">Chat History</div>
                        {sessions.map(s => (
                            <div key={s.chat_id} 
                                 onClick={() => { setActiveSessionId(s.chat_id); setDrawerOpen(false); }}
                                 className={`w-chat-history-item${s.chat_id === activeSessionId ? ' active' : ''}`}>
                                <span className="text-sm text-[#dadada] truncate">{s.title}</span>
                                <button onClick={(e) => { e.stopPropagation(); deleteSession(s.chat_id); }} className="w-chat-history-delete">
                                    <Trash2 size={12} />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            <div className="w-chat-messages">
                {messages.length === 0 && <div className="w-chat-message-empty">Start a new conversation...</div>}
                {messages.map((msg) => (
                    <div key={msg.id} className={`w-chat-message ${msg.role}`}>
                        <div className={`w-chat-bubble ${msg.role}`}>{msg.text}</div>
                        <span className="w-chat-timestamp">{new Date(msg.ts).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>
                    </div>
                ))}
                {isTyping && <div className="w-chat-typing">AI is thinking...</div>}
                <div ref={messagesEndRef} />
            </div>
            <div className="w-chat-input-area">
                <div className="w-chat-input-wrapper">
                    <input 
                        type="text" 
                        className="w-chat-input"
                        placeholder="Type a message..."
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && sendMessage()}
                    />
                    <button 
                        onClick={sendMessage}
                        className="w-chat-send-btn"
                        disabled={!input.trim() || isTyping}
                    >
                        <Send size={16} />
                    </button>
                </div>
            </div>
        </div>
    );
};
export default ChatSidebar;
