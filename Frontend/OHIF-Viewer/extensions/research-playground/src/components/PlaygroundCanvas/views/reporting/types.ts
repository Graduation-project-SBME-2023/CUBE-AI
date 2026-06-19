export interface ReportingViewProps {
    reportData?: any;
    latexData?: any;
    sessionId?: string;
}

export type CompileStatus = 'idle' | 'compiling' | 'success' | 'error';
