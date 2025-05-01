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
    
    // Handle simulate call form
    const simulateCallForm = document.getElementById('simulateCallForm');
    if (simulateCallForm) {
        simulateCallForm.addEventListener('submit', function(e) {
            e.preventDefault();
            
            const customerId = document.getElementById('customerId').value.trim();
            const question = document.getElementById('customerQuestion').value.trim();
            
            if (!customerId || !question) {
                alert('Please fill in all fields');
                return;
            }
            
            // Display simulation results
            const simulationResults = document.getElementById('simulationResults');
            const simulationOutput = document.getElementById('simulationOutput');
            
            simulationResults.style.display = 'block';
            simulationOutput.innerHTML = '';
            
            // Simulate agent processing
            addMessage(simulationOutput, 'system', 'Incoming call from customer ' + customerId);
            addMessage(simulationOutput, 'customer', question);
            
            // Check if the question is about something our fictional salon knows
            const knownTopics = ['hours', 'location', 'address', 'services', 'haircut', 'color', 'pricing'];
            let knownQuestion = false;
            
            for (const topic of knownTopics) {
                if (question.toLowerCase().includes(topic)) {
                    knownQuestion = true;
                    break;
                }
            }
            
            if (knownQuestion) {
                // Simulate AI response for known information
                setTimeout(() => {
                    addMessage(simulationOutput, 'ai', 'I have that information for you! [AI provides answer]');
                }, 1000);
            } else {
                // Simulate AI escalation for unknown information
                setTimeout(() => {
                    addMessage(simulationOutput, 'ai', 'Let me check with my supervisor and get back to you.');
                    
                    setTimeout(() => {
                        addMessage(simulationOutput, 'system', 'Help request created: ' + question);
                        addMessage(simulationOutput, 'system', 'Supervisor notification sent');
                        
                        // Add a button to resolve the simulated request
                        const resolveDiv = document.createElement('div');
                        resolveDiv.className = 'mt-3 border rounded p-3 bg-white';
                        resolveDiv.innerHTML = `
                            <h6>Supervisor Interface</h6>
                            <div class="mb-3">
                                <label class="form-label">Your Answer:</label>
                                <textarea class="form-control" id="simulatedAnswer" rows="2"></textarea>
                            </div>
                            <button id="simulatedResolve" class="btn btn-sm btn-success">Resolve</button>
                        `;
                        simulationOutput.appendChild(resolveDiv);
                        
                        // Handle the simulated resolve
                        document.getElementById('simulatedResolve').addEventListener('click', function() {
                            const answer = document.getElementById('simulatedAnswer').value.trim();
                            if (answer) {
                                resolveDiv.remove();
                                addMessage(simulationOutput, 'system', 'Request resolved with answer: ' + answer);
                                addMessage(simulationOutput, 'system', 'Knowledge base updated');
                                addMessage(simulationOutput, 'system', 'Customer notified via text message');
                            } else {
                                alert('Please provide an answer');
                            }
                        });
                    }, 1500);
                }, 1000);
            }
        });
    }
    
    function addMessage(container, type, message) {
        const messageDiv = document.createElement('div');
        messageDiv.className = type + '-message';
        
        if (type === 'system') {
            messageDiv.textContent = '🔧 SYSTEM: ' + message;
        } else if (type === 'ai') {
            messageDiv.textContent = '🤖 AI: ' + message;
        } else if (type === 'customer') {
            messageDiv.textContent = '👤 CUSTOMER: ' + message;
        }
        
        container.appendChild(messageDiv);
    }
});