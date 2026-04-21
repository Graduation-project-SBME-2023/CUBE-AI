import React from 'react';
import clsx from 'clsx';
import theme from './theme';

type Variant = 'primary' | 'secondary' | 'agentic';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export default function Button({ variant = 'primary', className, children, ...rest }: ButtonProps) {
  const base = 'px-4 py-2 rounded-lg font-semibold transition-colors inline-flex items-center gap-2';
  const variantClasses = {
    primary: 'text-white hover:brightness-110',
    secondary: 'bg-[#151a1f] border text-[#91b9cd] hover:brightness-110',
    agentic: 'text-black hover:brightness-95',
  };

  const variantStyles: Record<Variant, React.CSSProperties> = {
    primary: {
      backgroundColor: theme.colors.accentBlue,
      borderColor: 'transparent',
    },
    secondary: {
      borderColor: theme.colors.primary,
    },
    agentic: {
      backgroundColor: theme.colors.primary,
      borderColor: 'transparent',
    },
  };

  return (
    <button
      className={clsx(base, variantClasses[variant], className)}
      style={variantStyles[variant]}
      {...rest}
    >
      {children}
    </button>
  );
}
