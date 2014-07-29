jQuery(document).ready(function() {
     var bloodhoundBase = getBaseUrl();
	$('input#field-summary.input-block-level').blur(function() {
		var text = $('input#field-summary.input-block-level').val();
		if (text.length > 0) {

			var html = '<h5 class="loading">Loading related tickets..</h5>';
			var dupelicate_ticket_list_div = $('div.popover-content input#field-summary + div#dupeticketlist');
			if (dupelicate_ticket_list_div.length == 0) {
				$('div.popover-content input#field-summary').after('<div id="dupeticketlist" style="display:none;"></div>');
				dupelicate_ticket_list_div = $('div.popover-content input#field-summary + div#dupeticketlist');
			}
			$(dupelicate_ticket_list_div).slideUp('fast');
			dupelicate_ticket_list_div.html(html).slideDown();

			$.ajax({
				url:bloodhoundBase.url+'duplicate_ticket_search',
                data:{q:text},
				type:'GET',
				success: function(data, status) {
					var tickets =data;
					var ticket_base_href =  'ticket/';
					var search_base_Href =  'bhsearch?type=ticket&q=';
					var max_tickets = 5;

					var html = '';
					if (tickets === null) {
						// error
						dupelicate_ticket_list_div.html('<h5 class="error">Error loading tickets.</h5>');
					} else if (tickets.length <= 0) {
						// no dupe tickets
						dupelicate_ticket_list_div.slideUp();
					} else {
						html = '<h5>Possible related tickets:</h5><ul id="results" style="display:none; list-style-type: none">';
						//tickets = tickets.reverse();

						for (var i = 0; i < tickets.length && i < max_tickets; i++) {
							var ticket = tickets[i];
							html += '<li class="highlight_matches" title="' + ticket.description +
								    '"><a href="' + ticket_base_href + ticket.url +
								    '"><span class="' + htmlencode(ticket.status) + '">#' +
								    ticket.url + '</span></a>: ' + htmlencode(ticket.type) + ': ' +
								    ticket.summary + '(' + htmlencode(ticket.status) +
								    (ticket.url ? ': ' + htmlencode(ticket.url) : '') +
								    ')' + '</li>'
						}
						html += '</ul>';
						if (tickets.length > max_tickets) {
							var text = $('div.popover-content input#field-summary').val();
							html += '<a href="' + search_base_Href + escape(text) + '">More..</a>';
						}

						dupelicate_ticket_list_div.html(html);
						$('> ul', dupelicate_ticket_list_div).slideDown();

					}

				},
				error: function(xhr, textStatus, exception) {
					dupelicate_ticket_list_div.html('<h5 class="error">Error loading tickets: ' + textStatus + '</h5>');
				}
			});
		}else{
            var dupelicate_ticket_list_div = $('div.popover-content input#field-summary + div#dupeticketlist');
            dupelicate_ticket_list_div.slideUp();
        }
	});

	function htmlencode(text) {
		return $('<div/>').text(text).html().replace(/"/g, '&quot;').replace(/'/g, '&apos;');
	}
    	function getBaseUrl() {
		// returns the base URL to bloodhound, based on guesses.
		var returnVal = { url:null, ticket:null };
		var urlRegex = /^.+?(?=\/newticket.*|\/ticket\/(\d+).*|\/ticket.*)/i;
		var match = urlRegex.exec(location.href);
		if (match) {
			if (match[1]) {
				// also have a ticket number
				returnVal.ticket = match[1];
			}
			returnVal.url = match[0] + (match[0].match('/$') ? '' : '/');
		} else {
			//check whether the url is base url, if base url add ending '/'
            var urlRegex1 = /^.+?(?=\/.*)/i;
            var match1 = urlRegex1.exec(location.pathname);
            if(match1){
                returnVal.url = ''
            }else{
                returnVal.url = location.href + (location.href.match('/$') ? '' : '/') ;
            }
		}
		return returnVal;
	}
});

