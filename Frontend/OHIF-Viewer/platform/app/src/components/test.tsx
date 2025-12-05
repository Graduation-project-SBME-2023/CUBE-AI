import React, { useEffect, useRef, useState } from "react";
import websocketService from "../utils/WebsocketService";

type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: number;
};

const makeId = (prefix = "id") => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;

const SimpleChatWidget: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Connect WebSocket and handle messages
  useEffect(() => {
    websocketService.connect();

    const unsubscribe = websocketService.subscribe((msg: any) => {
      console.log("[WS] received:", msg);
      if (msg.type && msg.payload) {
        // Always append payload as a message
        setMessages((prev) => [
          ...prev,
          {
            id: makeId("a"),
            role: "assistant",
            text: msg.payload.text || JSON.stringify(msg.payload),
            ts: Date.now(),
          },
        ]);
        setIsTyping(false);
      }
    });

    return () => unsubscribe();
  }, []);

  const sendMessage = () => {
    if (!input.trim()) return;

    const userMsg: Message = { id: makeId("u"), role: "user", text: input, ts: Date.now() };
    setMessages((prev) => [...prev, userMsg]);
    setIsTyping(true);

    websocketService.sendMessage({ type: "chat", payload: { text: input } });

    setInput("");
  };

  // Scroll to bottom when messages change
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="simple-chat-widget">
      <div className="chat-messages" ref={listRef}>
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            {m.text}
          </div>
        ))}
        {isTyping && <div className="message assistant">💭 Typing...</div>}
      </div>
      <div className="chat-input">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
        />
        <button onClick={sendMessage}>Send</button>
      </div>
    </div>
  );
};

export default SimpleChatWidget;
