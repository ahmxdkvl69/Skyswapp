import io
from fpdf import FPDF

class SkySwapPDF(FPDF):
    def header(self):
        self.set_font('helvetica', 'B', 16)
        self.set_text_color(29, 78, 216) # Blue color
        self.cell(0, 10, 'SkySwap', border=False, align='L')
        self.set_font('helvetica', 'I', 12)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, 'Flight Ticket Resale Platform', border=False, align='R')
        self.ln(15)
        # Line break
        self.set_line_width(0.5)
        self.set_draw_color(200, 200, 200)
        self.line(10, 25, 200, 25)

    def footer(self):
        self.set_y(-15)
        self.set_font('helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Page {self.page_no()}', align='C')

def generate_ticket_pdf(data):
    """
    Generate PDF for a single ticket.
    Returns bytes.
    """
    pdf = SkySwapPDF()
    pdf.add_page()
    
    def add_section(title, fields):
        pdf.set_font("helvetica", 'B', 14)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 10, title, ln=True)
        pdf.set_font("helvetica", size=11)
        pdf.set_text_color(71, 85, 105)
        
        for key, val in fields.items():
            pdf.set_font("helvetica", 'B', 11)
            pdf.cell(50, 8, f"{key}:")
            pdf.set_font("helvetica", '', 11)
            pdf.cell(0, 8, str(val), ln=True)
        pdf.ln(5)

    add_section("Ticket Summary", {
        "Ticket ID": data.get('ticket_id', 'N/A'),
        "Flight Number": data.get('flight_number', 'N/A'),
        "Airline Name": data.get('airline', 'N/A'),
        "Departure City": data.get('origin', 'N/A'),
        "Destination City": data.get('destination', 'N/A'),
        "Departure Date": data.get('departure_date', 'N/A'),
        "Departure Time": data.get('departure_time', 'N/A'),
        "Arrival Time": data.get('arrival_time', 'N/A'),
        "Flight Duration": data.get('duration', 'N/A'),
        "Seat Number": data.get('seat_number', 'N/A'),
        "Travel Class": data.get('travel_class', 'N/A')
    })
    
    add_section("Seller Information", {
        "Seller Name": data.get('seller_name', 'N/A'),
        "User ID": data.get('seller_uid', 'N/A'),
        "Contact Number": data.get('seller_phone', 'N/A'),
        "Email Address": data.get('seller_email', 'N/A')
    })

    add_section("Resale Information", {
        "Original Ticket Price": f"PKR {data.get('original_price', 0):,}",
        "Resale Price": f"PKR {data.get('resale_price', 0):,}",
        "Reason for Resale": data.get('resale_reason', 'N/A')
    })

    add_section("Verification Information", {
        "Verification Status": data.get('verification_status', 'Pending Verification'),
        "Verified By Admin": data.get('verified_by', 'N/A'),
        "Verification Date & Time": data.get('verification_date', 'N/A')
    })

    return bytes(pdf.output())

def generate_report_pdf(reports_data):
    """
    Generate PDF report for multiple tickets.
    Returns bytes.
    """
    pdf = SkySwapPDF()
    pdf.add_page()
    
    pdf.set_font("helvetica", 'B', 16)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 10, "Tickets Report", ln=True, align='C')
    pdf.ln(5)

    # Table Header
    pdf.set_font("helvetica", 'B', 9)
    pdf.set_fill_color(241, 245, 249)
    
    headers = ['Date', 'Flight', 'Route', 'Time', 'Seller', 'Status']
    col_widths = [25, 25, 45, 30, 40, 25]
    
    for header, width in zip(headers, col_widths):
        pdf.cell(width, 10, header, border=1, fill=True)
    pdf.ln()

    # Table Body
    pdf.set_font("helvetica", '', 8)
    for row in reports_data:
        route = f"{row.get('origin', '')[:3]}->{row.get('destination', '')[:3]}"
        time = f"{row.get('departure_time', '')}-{row.get('arrival_time', '')}"
        
        # Make sure texts fit
        seller_email = row.get('seller_email', '')[:20]
        
        pdf.cell(col_widths[0], 10, str(row.get('departure_date', '')), border=1)
        pdf.cell(col_widths[1], 10, str(row.get('flight_number', '')), border=1)
        pdf.cell(col_widths[2], 10, route, border=1)
        pdf.cell(col_widths[3], 10, time, border=1)
        pdf.cell(col_widths[4], 10, seller_email, border=1)
        pdf.cell(col_widths[5], 10, str(row.get('status', '')), border=1)
        pdf.ln()

    return bytes(pdf.output())
