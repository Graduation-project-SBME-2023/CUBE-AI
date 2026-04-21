import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, History, Plus, ChevronRight, Trash2, Send } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import websocketService from '../../utils/WebsocketService';
import './ChatSidebar.css';

const makeId = (prefix = 'id') => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
const renderIcon = (Icon, size) => React.createElement(Icon, { size, 'aria-hidden': true });

const ChatSidebar = ({
  isOpen,
  onClose,
  initialUserMessage,
  externalSessionId,
  sendInitialMessage = false,
  onPlanApprovalNeeded,
}) => {
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(420);
  const [isResizing, setIsResizing] = useState(false);
  const [maxSidebarWidth, setMaxSidebarWidth] = useState(() =>
    Math.max(340, Math.min(760, Math.floor(window.innerWidth * 0.55)))
  );
  const resizeStateRef = useRef({ startX: 0, startWidth: 420 });
  const messagesEndRef = useRef(null);
  const activeSessionIdRef = useRef(null);

  const MIN_WIDTH = 340;
  const MAX_WIDTH = maxSidebarWidth;
  const activeSession = sessions.find(s => String(s.chat_id) === String(activeSessionId)) || null;
  let messages = activeSession ? activeSession.messages : [];
  // If there are no messages and an initialUserMessage is provided, show it as the first user message
  if (messages.length === 0 && initialUserMessage) {
    messages = [{ id: makeId('init'), role: 'user', text: initialUserMessage, ts: Date.now() }];
  }

  useEffect(() => {
    console.log('[HypothesisFlow][ChatSidebar] Session binding effect', {
      externalSessionId,
      sendInitialMessage,
      hasInitialUserMessage: Boolean(initialUserMessage),
      initialUserMessageLength: initialUserMessage?.length || 0,
    });

    if (externalSessionId) {
      setActiveSessionId(externalSessionId);
      console.log('[HypothesisFlow][ChatSidebar] Active session set from route state', {
        externalSessionId,
      });
      // Ensure a local session entry exists for this external ID
      setSessions(prev => {
        const exists = prev.some(s => String(s.chat_id) === String(externalSessionId));
        if (!exists) {
          return [{
            chat_id: externalSessionId,
            title: 'Research Session',
            messages: initialUserMessage
              ? [{ id: makeId('init'), role: 'user', text: initialUserMessage, ts: Date.now() }]
              : [],
            summary: '',
            last_activity: Date.now(),
          }, ...prev];
        }
        return prev;
      });
      // Optionally refresh sessions list so the new one appears in history
      fetchChatSessions();
    }
    if (sendInitialMessage) {
      // if (websocketService.isConnected()) {
      //   websocketService.sendMessage({ type: 'user', payload: initialUserMessage });
      // } else {
      //   alert('WebSocket not connected. Message not sent to backend in real time.');
      // }
      console.log('[HypothesisFlow][ChatSidebar] Sending initial message to chat backend', {
        externalSessionId,
        preview: (initialUserMessage || '').slice(0, 120),
      });
      fetch(`http://localhost:8000/chat_messages/${externalSessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: 'user', text: initialUserMessage }),
      })
        .then(response => {
          console.log('[HypothesisFlow][ChatSidebar] Initial message POST result', {
            ok: response.ok,
            status: response.status,
            externalSessionId,
          });
        })
        .catch(error => {
          console.error('[HypothesisFlow][ChatSidebar] Failed to send initial message', {
            externalSessionId,
            error,
          });
        });
    }
  }, [externalSessionId]);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    if (!isResizing) {
      return undefined;
    }

    const handleMouseMove = event => {
      const delta = resizeStateRef.current.startX - event.clientX;
      const next = Math.max(
        MIN_WIDTH,
        Math.min(MAX_WIDTH, resizeStateRef.current.startWidth + delta)
      );
      setSidebarWidth(next);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  useEffect(() => {
    const handleWindowResize = () => {
      const nextMax = Math.max(340, Math.min(760, Math.floor(window.innerWidth * 0.55)));
      setMaxSidebarWidth(nextMax);
      setSidebarWidth(prev => Math.min(prev, nextMax));
    };

    window.addEventListener('resize', handleWindowResize);
    return () => {
      window.removeEventListener('resize', handleWindowResize);
    };
  }, []);

  const handleResizeStart = event => {
    resizeStateRef.current = {
      startX: event.clientX,
      startWidth: sidebarWidth,
    };
    setIsResizing(true);
  };

  const fetchChatSessions = async () => {
    try {
      const r = await fetch('http://localhost:8000/chat_sessions/');
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const data = await r.json();
      const mapped = (data || []).map(s => ({
        chat_id: s.chat_id,
        title: s.title || `Chat ${s.chat_id}`,
        messages: [],
        summary: s.summary,
        last_activity: s.last_activity ? new Date(s.last_activity).getTime() : 0,
      }));
      // Merge DB sessions with existing local sessions to preserve WS-delivered messages
      setSessions(prev => {
        const merged = [...prev];
        for (const dbSession of mapped) {
          const idx = merged.findIndex(s => String(s.chat_id) === String(dbSession.chat_id));
          if (idx === -1) {
            merged.push(dbSession);
          } else {
            // Keep existing messages (from WS) if DB returned none
            merged[idx] = {
              ...dbSession,
              messages: merged[idx].messages.length > 0 ? merged[idx].messages : dbSession.messages,
            };
          }
        }
        return merged;
      });
    } catch (err) {}
  };
  const fetchMessagesForSession = async chat_id => {
    try {
      const res = await fetch(`http://localhost:8000/chat_messages/${chat_id}`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const msgs = await res.json();
      const dbMessages = msgs.map(m => ({
        id: `db_${m.message_id}`,
        role: m.role,
        text: m.text,
        ts: new Date(m.timestamp).getTime(),
      }));
      setSessions(prev =>
        prev.map(s => {
          if (String(s.chat_id) !== String(chat_id)) return s;
          // Merge: use DB messages as base, append any local-only messages not in DB
          const dbIds = new Set(dbMessages.map(m => m.id));
          const localOnly = s.messages.filter(m => !dbIds.has(m.id) && !m.id.startsWith('db_'));
          return { ...s, messages: [...dbMessages, ...localOnly] };
        })
      );
    } catch (err) {}
  };
  const createNewSession = async () => {
    try {
      const payload = { title: `Chat ${sessions.length + 1}` };
      const r = await fetch('http://localhost:8000/chat_sessions/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const data = await r.json();
      const newSession = {
        chat_id: data.chat_id,
        title: data.title || payload.title,
        messages: [
          {
            id: makeId('sys'),
            role: 'system',
            text: 'Hello! I am your research assistant.',
            ts: Date.now(),
          },
        ],
        summary: data.summary,
        last_activity: Date.now(),
      };
      setSessions(s => [newSession, ...s]);
      setActiveSessionId(newSession.chat_id);
      setDrawerOpen(false);
    } catch (err) {
      // Fallback: create a local-only session if DB fails
      const fallbackId = Date.now();
      const newSession = {
        chat_id: fallbackId,
        title: `Local Chat ${sessions.length + 1}`,
        messages: [
          {
            id: makeId('sys'),
            role: 'system',
            text: 'Hello! (local session, DB unavailable)',
            ts: Date.now(),
          },
        ],
        summary: '',
        last_activity: Date.now(),
      };
      setSessions(s => [newSession, ...s]);
      setActiveSessionId(newSession.chat_id);
      setDrawerOpen(false);
      alert(
        'Warning: Could not create chat session in the database. Using a local-only session.\n\nError: ' +
          err
      );
    }
  };
  const deleteSession = async chat_id => {
    try {
      await fetch(`http://localhost:8000/chat_sessions/${chat_id}`, { method: 'DELETE' });
    } catch (err) {}
    setSessions(s => s.filter(x => x.chat_id !== chat_id));
    if (activeSessionId === chat_id) {
      setActiveSessionId(null);
    }
  };
  const sendMessage = async (messageText?: string) => {
    const textToSend = messageText || input.trim();
    if (!textToSend) {
      return;
    }
    let sessionId = activeSessionId;
    // If no session, create one and wait for state update
    if (!sessionId) {
      await createNewSession();
      sessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;
      if (!sessionId) {
        return;
      }
    }
    const userMsg = { id: makeId('u'), role: 'user', text: textToSend, ts: Date.now() };
    setSessions(prev =>
      prev.map(s =>
        String(s.chat_id) === String(sessionId)
          ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() }
          : s
      )
    );
    if (!messageText) {
      setInput('');
    }
    setIsTyping(true);
    try {
      await fetch(`http://localhost:8000/chat_messages/${sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: 'user', text: userMsg.text }),
      });
    } catch (e) {
      alert(
        'Warning: Could not save message to the database. This chat is now local-only.\n\nError: ' +
          e
      );
    }
    // Debug log outgoing message
    console.log('[ChatSidebar] Sending WS message:', { type: 'user', payload: userMsg.text });
    if (websocketService.isConnected()) {
      websocketService.sendMessage({
        type: 'user',
        payload: { text: userMsg.text, session_id: sessionId },
        session_id: sessionId,
      });
    } else {
      alert('WebSocket not connected. Message not sent to backend in real time.');
    }
  };
  // Only connect WebSocket once for the app session (not just when sidebar is open)
  useEffect(() => {
    websocketService.connect();
    fetchChatSessions();
    const handleWS = async data => {
      console.log('[ChatSidebar] WS received:', data);

      // Handle echoed user messages from notification buttons
      if (data.type === 'chat_echo') {
        const currentSessionId = activeSessionIdRef.current;
        if (!currentSessionId) return;
        const incomingSessionId = String(data.session_id);
        if (incomingSessionId && incomingSessionId !== String(currentSessionId)) return;
        const userMsg = {
          id: makeId('u'),
          role: 'user',
          text: data.payload?.text || data.payload || '',
          ts: Date.now(),
        };
        setSessions(prev =>
          prev.map(s =>
            String(s.chat_id) === String(currentSessionId)
              ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() }
              : s
          )
        );
        setIsTyping(true);
        try {
          await fetch(`http://localhost:8000/chat_messages/${currentSessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: 'user', text: userMsg.text }),
          });
        } catch (err) {}
        return;
      }

      if (data.type !== 'msg') {
        return;
      }

      const incomingSessionId = String(data.session_id);
      if (incomingSessionId && activeSessionIdRef.current && incomingSessionId !== String(activeSessionIdRef.current)) {
        return;
      }

      // const currentSessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;
      const currentSessionId = activeSessionIdRef.current;
      console.log(currentSessionId);
      if (!currentSessionId) {
        return;
      }
      const textContent =
        typeof data.payload === 'string'
          ? data.payload
          : data.payload.text || JSON.stringify(data.payload);

      // Don't display raw plan JSON in chat — it belongs in the playground canvas
      const trimmed = textContent.trim();
      if (trimmed.startsWith('{') && trimmed.includes('"steps"')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed.steps && Array.isArray(parsed.steps)) {
            return;
          }
        } catch (_) { /* not valid JSON, show as normal */ }
      }

      const assistantMsg = {
        id: makeId('a'),
        role: 'assistant',
        text: textContent,
        ts: Date.now(),
      };
      console.log(assistantMsg);
      setSessions(prev => {
        const match = prev.find(s => String(s.chat_id) === String(currentSessionId));
        if (match) {
          return prev.map(s =>
            String(s.chat_id) === String(currentSessionId)
              ? { ...s, messages: [...s.messages, assistantMsg], last_activity: Date.now() }
              : s
          );
        }
        // No session exists yet — create one with this message
        return [{
          chat_id: currentSessionId,
          title: 'Research Session',
          messages: [assistantMsg],
          summary: '',
          last_activity: Date.now(),
        }, ...prev];
      });
      setIsTyping(false);
      try {
        await fetch(`http://localhost:8000/chat_messages/${currentSessionId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: 'assistant', text: assistantMsg.text }),
        });
      } catch (err) {}
    };
    const unsubscribe = websocketService.subscribe(handleWS);
    return () => {
      unsubscribe();
    };
  }, []);
  useEffect(() => {
    console.log(activeSessionId);
    if (activeSessionId) {
      fetchMessagesForSession(activeSessionId);
    }
  }, [activeSessionId]);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  // Detect plan approval requests and notify parent
  useEffect(() => {
    if (messages && messages.length > 0 && onPlanApprovalNeeded) {
      const lastMessage = messages[messages.length - 1];
      if (
        lastMessage.role === 'assistant' &&
        lastMessage.text &&
        lastMessage.text.includes('Review the pipeline plan')
      ) {
        onPlanApprovalNeeded();
      }
    }
  }, [messages, onPlanApprovalNeeded]);
  if (!isOpen) {
    return null;
  }
  return (
    <div
      className={`w-chat-sidebar${isResizing ? ' is-resizing' : ''}`}
      style={{ width: `${sidebarWidth}px` }}
    >
      <button
        type="button"
        className="w-chat-resize-handle"
        aria-label="Resize chat sidebar"
        onMouseDown={handleResizeStart}
      />
      <div className="w-chat-header">
        <div className="w-chat-header-title">
          {renderIcon(MessageSquare, 16)} Assistant
        </div>
        <div className="w-chat-header-actions">
          <button
            onClick={() => setDrawerOpen(!drawerOpen)}
            title="History"
          >
            {renderIcon(History, 16)}
          </button>
          <button
            onClick={() => createNewSession()}
            title="New Chat"
          >
            {renderIcon(Plus, 16)}
          </button>
          <button
            onClick={onClose}
            style={{ marginLeft: 8 }}
          >
            {renderIcon(ChevronRight, 18)}
          </button>
        </div>
      </div>
      {drawerOpen && (
        <div className="w-chat-history-drawer">
          <div className="w-chat-history-list">
            <div className="w-chat-history-title">Chat History</div>
            {sessions.map(s => (
              <div
                key={s.chat_id}
                onClick={() => {
                  setActiveSessionId(s.chat_id);
                  setDrawerOpen(false);
                }}
                className={`w-chat-history-item${s.chat_id === activeSessionId ? ' active' : ''}`}
              >
                <span className="truncate text-sm text-[#dadada]">{s.title}</span>
                <button
                  onClick={e => {
                    e.stopPropagation();
                    deleteSession(s.chat_id);
                  }}
                  className="w-chat-history-delete"
                >
                  {renderIcon(Trash2, 12)}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="w-chat-messages">
        {messages.length === 0 && (
          <div className="w-chat-message-empty">Start a new conversation...</div>
        )}
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`w-chat-message ${msg.role}`}
          >
            <div className={`w-chat-bubble ${msg.role}`}>
              <div className="w-chat-markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text || ''}</ReactMarkdown>
              </div>
            </div>
            <span className="w-chat-timestamp">
              {new Date(msg.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        {isTyping && <div className="w-chat-typing">CUBE is thinking...</div>}
        <div ref={messagesEndRef} />
      </div>
      <div className="w-chat-input-area">
        <div className="w-chat-input-wrapper">
          <input
            type="text"
            className="w-chat-input"
            placeholder="Type a message..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendMessage()}
          />
          <button
            onClick={sendMessage}
            className="w-chat-send-btn"
            disabled={!input.trim() || isTyping}
          >
            {renderIcon(Send, 16)}
          </button>
        </div>
      </div>
    </div>
  );
};
export default ChatSidebar;
