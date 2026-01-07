import React from 'react';
import clsx from 'clsx';
import theme from '../../theme';

type Variant = 'primary' | 'secondary' | 'agentic';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export default function Button({ variant = 'primary', className, children, ...rest }: ButtonProps) {
  const base = 'px-4 py-2 rounded-lg font-semibold transition-colors inline-flex items-center gap-2';
  const variantClasses = {
    primary: `bg-[${theme.colors.accentBlue}] text-white hover:brightness-110`,
    secondary: `bg-[#151a1f] border border-[#3c5d80] text-[#91b9cd] hover:border-[${theme.colors.primary}]`,
    agentic: `bg-[${theme.colors.primary}] text-black hover:brightness-95`,
  };

  return (
    <button className={clsx(base, variantClasses[variant], className)} {...rest}>
      {children}
    </button>
  );
}
