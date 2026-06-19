import React from 'react';

interface SidebarProps {
  className?: string;
  children: React.ReactNode;
}

export default function Sidebar({ className = '', children }: SidebarProps) {
  return <aside className={`flex h-full flex-col bg-[#0b1015] ${className}`}>{children}</aside>;
}
