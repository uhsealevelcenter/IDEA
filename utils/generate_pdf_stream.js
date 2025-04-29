// For puppeteer pdf generation (under development)
// generate_pdf_stream.js
const puppeteer = require('puppeteer');

let html = '';

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => html += chunk);
process.stdin.on('end', async () => {
  try {
    const browser = await puppeteer.launch({
      executablePath: '/usr/bin/chromium', // <--- point to system chromium
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'networkidle0' });
    const pdf = await page.pdf({ format: 'A4', printBackground: true });
    await browser.close();
    process.stdout.write(pdf);
  } catch (err) {
    console.error(err);
    process.exit(1);
  }
});