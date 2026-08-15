import * as React from "react";
export const Input=React.forwardRef<HTMLInputElement,React.InputHTMLAttributes<HTMLInputElement>>(function Input({className="",...props},ref){return <input ref={ref} className={`shadcn-input ${className}`} {...props}/>;});
