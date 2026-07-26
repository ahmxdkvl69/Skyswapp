import re

with open('c:/Users/USER/Downloads/SKYSWAP/SKYSWAP/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# We want to remove all the messed up end of file and replace it with the clean version
# We will match from @app.route('/reset-new-password') to the end of the file

match = re.search(r"@app\.route\('/reset-new-password', methods=\['GET', 'POST'\]\)", content)
if match:
    start_idx = match.start()
    
    clean_tail = """@app.route('/reset-new-password', methods=['GET', 'POST'])
def reset_new_password():

    if 'reset_email' not in session:
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':

        password = request.form['password']
        email = session['reset_email']

        if password_strength(password) == "weak":
            flash_error("Please choose a stronger password")
            return redirect(url_for('reset_new_password'))

        users_collection.update_one(
            {'email': email},
            {
                '$set': {
                    'password': hash_password(password)
                }
            }
        )

        session.pop('reset_email', None)
        session.pop('reset_otp', None)

        flash_success("Password reset successfully")
        return redirect(url_for('index'))

    return render_template('reset_new_password.html')

@app.route('/admin/tickets')
def admin_tickets():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))
    
    search = request.args.get('search', '').lower()
    status_filter = request.args.get('status', '')
    
    query = {}
    if status_filter:
        query['status'] = status_filter
        
    sell_requests = list(sell_requests_collection.find(query))
    filtered_requests = []
    
    for req in sell_requests:
        req['_id'] = str(req.get('_id', ''))
        
        booking = bookings_collection.find_one({"booking_id": req['booking_id']})
        if booking:
            req['airline'] = booking.get('airline', 'N/A')
            req['departure_date'] = booking.get('departure_date', 'N/A')
            req['departure_time'] = booking.get('departure', 'N/A')
            req['destination'] = booking.get('destination', 'N/A')
        else:
            req['airline'] = 'N/A'
            req['departure_date'] = 'N/A'
            req['departure_time'] = 'N/A'
            req['destination'] = 'N/A'

        if search:
            if search not in str(req.get('request_id', '')).lower() and \
               search not in str(req.get('flight_number', '')).lower() and \
               search not in str(req.get('seller_email', '')).lower() and \
               search not in str(req.get('airline', '')).lower():
                continue
            
        filtered_requests.append(req)
        
    return render_template('admin_tickets.html', tickets=filtered_requests)

@app.route('/admin/ticket/<request_id>')
def admin_ticket_detail(request_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))
        
    sell_req = sell_requests_collection.find_one({"request_id": request_id})
    if not sell_req:
        flash_error("Ticket request not found")
        return redirect(url_for('admin_tickets'))
        
    booking = bookings_collection.find_one({"booking_id": sell_req['booking_id']})
    seller = users_collection.find_one({"uid": sell_req['seller_uid']})
    
    return render_template('admin_ticket_detail.html', ticket=sell_req, booking=booking, seller=seller)

@app.route('/admin/verify-ticket/<request_id>')
def verify_ticket(request_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))
        
    status = request.args.get('status', '')
    if status not in ['Verified / Approved', 'Rejected']:
        flash_error("Invalid status")
        return redirect(url_for('admin_ticket_detail', request_id=request_id))
        
    sell_requests_collection.update_one(
        {"request_id": request_id},
        {
            "$set": {
                "status": status,
                "verification_date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "verified_by": session['user']['name']
            }
        }
    )
    
    flash_success(f"Ticket has been {status.lower()}")
    return redirect(url_for('admin_ticket_detail', request_id=request_id))

@app.route('/admin/ticket/<request_id>/pdf')
def admin_ticket_pdf(request_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))
        
    sell_req = sell_requests_collection.find_one({"request_id": request_id})
    if not sell_req:
        return "Not found", 404
        
    booking = bookings_collection.find_one({"booking_id": sell_req['booking_id']})
    seller = users_collection.find_one({"uid": sell_req['seller_uid']})
    
    data = {
        'ticket_id': request_id,
        'flight_number': sell_req.get('flight_number'),
        'airline': booking.get('airline', 'N/A') if booking else 'N/A',
        'origin': sell_req.get('origin'),
        'destination': sell_req.get('destination'),
        'departure_date': booking.get('departure_date', 'N/A') if booking else 'N/A',
        'departure_time': booking.get('departure', 'N/A') if booking else 'N/A',
        'arrival_time': booking.get('arrival', 'N/A') if booking else 'N/A',
        'duration': predict_flight_time(sell_req.get('origin', ''), sell_req.get('destination', '')).get('flight_time', 'N/A'),
        'seat_number': booking.get('seat_number', 'N/A') if booking else 'N/A',
        'travel_class': booking.get('travel_class', 'N/A') if booking else 'N/A',
        
        'seller_name': seller.get('name', 'N/A') if seller else 'N/A',
        'seller_uid': sell_req.get('seller_uid'),
        'seller_phone': seller.get('phone', 'N/A') if seller else 'N/A',
        'seller_email': sell_req.get('seller_email'),
        
        'original_price': booking.get('price', 0) if booking else 0,
        'resale_price': sell_req.get('asking_price', 0),
        'resale_reason': sell_req.get('resale_reason', 'N/A'),
        
        'verification_status': sell_req.get('status', 'Pending Verification'),
        'verified_by': sell_req.get('verified_by', 'N/A'),
        'verification_date': sell_req.get('verification_date', 'N/A')
    }
    
    pdf_bytes = generate_ticket_pdf(data)
    
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": f"attachment; filename=ticket_{request_id}.pdf"}
    )

@app.route('/admin/reports')
def admin_reports():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))
        
    sell_requests = list(sell_requests_collection.find({}))
    reports_data = []
    
    for req in sell_requests:
        booking = bookings_collection.find_one({"booking_id": req['booking_id']})
        reports_data.append({
            'departure_date': booking.get('departure_date', 'N/A') if booking else 'N/A',
            'flight_number': req.get('flight_number', ''),
            'origin': req.get('origin', ''),
            'destination': req.get('destination', ''),
            'departure_time': booking.get('departure', '') if booking else '',
            'arrival_time': booking.get('arrival', '') if booking else '',
            'seller_email': req.get('seller_email', ''),
            'status': req.get('status', 'Unknown')
        })
        
    pdf_bytes = generate_report_pdf(reports_data)
    
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": "attachment; filename=tickets_report.pdf"}
    )

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
"""
    new_content = content[:start_idx] + clean_tail
    with open('c:/Users/USER/Downloads/SKYSWAP/SKYSWAP/app.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
