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
  const location = useLocation();


    useEffect(() => {
    console.log("Current route:", location.pathname);
  }, [location]);

    // hide the default launcher button
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

const mockAgentResponse = async (text: string) => {
  try {
    const response = await fetch("http://localhost:8150/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: text }),
    });

    if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);

    const data = await response.json();
    console.log("raw response", response)
    console.log("Backend data:", data);
    console.log("markdown", data['markdown'])


    return data['markdown'];
  } catch (error) {
    console.error("Error connecting to backend:", error);
    return "⚠️ Could not reach the radiologist assistant server.";
  }
};


  const handleNewUserMessage = async (message: string) => {
    console.log("User message:", message);

    // add "thinking..." placeholder
    addResponseMessage("💭 Thinking...");

    const response = await mockAgentResponse(message);
    deleteMessages(1); // removes the last bot message (the thinking one) and add the real response :)
    addResponseMessage(response);
  };

  const handleToggle = () => {
    toggleWidget();
    setIsOpen(!isOpen);
  };

  return (
    <>
      {/* floating chat icon */}
      <button onClick={handleToggle} className="chat-launcher-button">
        <img src="/assets/chatbot.png" alt="Chat" className="chat-launcher-icon" />
      </button>

    
      {isOpen && (
        <button onClick={handleToggle} className="chat-close-button" title="Close chat">
          ×
        </button>
      )}

      {/* chat Widget */}
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
