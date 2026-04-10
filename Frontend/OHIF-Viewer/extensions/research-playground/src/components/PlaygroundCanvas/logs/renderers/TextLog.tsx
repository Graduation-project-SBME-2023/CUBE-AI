import React from 'react';

export function TextLog({ data }: { data: { message: string } }) {
  return (
    <div className="flex gap-2 text-gray-400">
      <span className="opacity-50 text-[#5acce6]">{'>'}</span>
      <span>{data.message}</span>
    </div>
  )
}
