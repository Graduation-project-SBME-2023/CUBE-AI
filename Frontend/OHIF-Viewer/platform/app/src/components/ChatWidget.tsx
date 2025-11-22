import React, { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Widget,
  addResponseMessage,
  deleteMessages,
  toggleWidget,
} from "react-chat-widget";
import "react-chat-widget/lib/styles.css";
import "./ChatWidget.css";

const ChatWidget: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [chatSessions, setChatSessions] = useState<any[]>([]); // new state
  const location = useLocation();

  // log current route
  useEffect(() => {
    console.log("Current route:", location.pathname);
  }, [location]);

  // hide default chat launcher
  useEffect(() => {
    const style = document.createElement("style");
    style.innerHTML = `
      .rcw-launcher {
        display: none !important;
      }
    `;
    document.head.appendChild(style);

    return () => {
      document.head.removeChild(style);
    };
  }, []);

  // fetch chat sessions from backend
  useEffect(() => {
    const fetchChatSessions = async () => {
      try {
        const response = await fetch("http://localhost:8000/chat_sessions/");
        if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
        const data = await response.json();
        setChatSessions(data); // update state
        console.log("Fetched chat sessions:", data);
      } catch (error) {
        console.error("Failed to fetch chat sessions:", error);
      }
    };

    fetchChatSessions();
  }, []);

  const mockAgentResponse = async (text: string) => {
    try {
      const response = await fetch("http://localhost:8150/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: text }),
      });

      if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);

      const data = await response.json();
      return data.response || "No response from agent.";
    } catch (error) {
      console.error("Error connecting to backend:", error);
      return "⚠️ Could not reach the radiologist assistant server.";
    }
  };

  const handleNewUserMessage = async (message: string) => {
    console.log("User message:", message);

    addResponseMessage("💭 Thinking...");

    const response = await mockAgentResponse(message);
    deleteMessages(1); // remove placeholder
    addResponseMessage(response);
  };

  const handleToggle = () => {
    toggleWidget();
    setIsOpen(!isOpen);
  };

  return (
    <>
      {/* Floating chat icon */}
      <button onClick={handleToggle} className="chat-launcher-button">
        <img src="/assets/chatbot.png" alt="Chat" className="chat-launcher-icon" />
      </button>

      {/* Custom controls */}
      {isOpen && (
        <div className="custom-controls">
          <button
            onClick={() => setIsDrawerOpen(true)}
            className="history"
            title="Chat History"
          >
            📜 History
          </button>

          <button
            onClick={() => {
              toggleWidget();
              setIsOpen(false);
              setIsDrawerOpen(false);
            }}
            className="chat-close-button"
            title="Close"
          >
            ×
          </button>
        </div>
      )}

      {/* Chat History Drawer */}
      {isDrawerOpen && (
        <>
          <div
            className="drawer-overlay"
            onClick={() => setIsDrawerOpen(false)}
          />
          <div className="drawer-container">
            <div className="drawer-header">
              <h2>Chat History</h2>
              <button
                onClick={() => setIsDrawerOpen(false)}
                className="drawer-close bg-red-600"
                title="Close"
              >
                ×
              </button>
            </div>
            <div className="drawer-content">
              {chatSessions.length === 0 ? (
                <p className="drawer-placeholder">Chat history will appear here.</p>
              ) : (
                <ul>
                  {chatSessions.map((session) => (
                    <li key={session.chat_id}>
                      <strong>{session.title}</strong> — {session.summary || "No summary"}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </>
      )}

      {/* React Chat Widget */}
      <Widget
        handleNewUserMessage={handleNewUserMessage}
        title="AI Assistant"
        subtitle="Hello Doctor, how can I help you today?"
        showCloseButton={false}
        emojis={true}
        profileAvatar="/assets/robot.png"
        resizable={true}
      />
    </>
  );
};

export default ChatWidget;
