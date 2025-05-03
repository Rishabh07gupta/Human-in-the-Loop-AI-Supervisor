document.addEventListener('DOMContentLoaded', function() {
    // Handle resolve form submissions
    const resolveForms = document.querySelectorAll('.resolve-form');
    resolveForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            const requestId = this.getAttribute('data-request-id');
            const answerField = this.querySelector('textarea[name="answer"]');
            const answer = answerField.value.trim();
            
            if (!answer) {
                alert('Please provide an answer');
                return;
            }
            
            // Send the resolve request
            fetch(`/resolve/${requestId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `answer=${encodeURIComponent(answer)}`
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert('Request resolved successfully!');
                    location.reload();
                } else {
                    alert(`Error: ${data.error}`);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('An error occurred while resolving the request');
            });
        });
    });
    
    // Handle mark unresolved button clicks
    const unresolvedButtons = document.querySelectorAll('.mark-unresolved');
    unresolvedButtons.forEach(button => {
        button.addEventListener('click', function() {
            const requestId = this.getAttribute('data-request-id');
            
            if (confirm('Are you sure you want to mark this request as unresolved?')) {
                // Send the unresolved request
                fetch(`/unresolved/${requestId}`, {
                    method: 'POST'
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Request marked as unresolved');
                        location.reload();
                    } else {
                        alert(`Error: ${data.error}`);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('An error occurred while marking the request as unresolved');
                });
            }
        });
    });
    
});