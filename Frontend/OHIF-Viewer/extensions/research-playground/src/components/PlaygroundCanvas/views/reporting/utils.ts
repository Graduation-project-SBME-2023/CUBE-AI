export function extractLatexText(latexData?: any): string {
    if (!latexData) return '';
    return (
        latexData?.latex_text ||
        latexData?.latex ||
        latexData?.outputs?.metrics?.latex_source ||
        latexData?.outputs?.metrics?.latex ||
        latexData?.outputs?.summary ||
        latexData?.data?.latex_text ||
        latexData?.data?.latex ||
        latexData?.outputs?.latex_text ||
        ''
    );
}

export function handlePdfDownload(pdfUrl: string, filename: string = 'Clinical_Hypothesis_Report.pdf') {
    if (!pdfUrl) return;
    const link = document.createElement('a');
    link.href = pdfUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
}
