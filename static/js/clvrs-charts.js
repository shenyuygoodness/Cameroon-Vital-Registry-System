document.addEventListener('DOMContentLoaded', function () {
    // Subdivision / region population bar chart
    const canvas = document.getElementById('subdivisionChart');
    if (canvas) {
        const labels = JSON.parse(canvas.dataset.labels || '[]');
        const data   = JSON.parse(canvas.dataset.chartdata || '[]');
        new Chart(canvas.getContext('2d'), {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: canvas.dataset.label || 'Population',
                    data: data,
                    backgroundColor: 'rgba(0,122,94,0.7)',
                    borderColor: 'rgba(0,122,94,1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                scales: { y: { beginAtZero: true } }
            }
        });
    }

    // Vital events trend chart
    const trendCanvas = document.getElementById('vitalTrendChart');
    if (trendCanvas) {
        const years     = JSON.parse(trendCanvas.dataset.years     || '[]');
        const births    = JSON.parse(trendCanvas.dataset.births    || '[]');
        const deaths    = JSON.parse(trendCanvas.dataset.deaths    || '[]');
        const marriages = JSON.parse(trendCanvas.dataset.marriages || '[]');
        const divorces  = JSON.parse(trendCanvas.dataset.divorces  || '[]');

        new Chart(trendCanvas.getContext('2d'), {
            type: 'bar',
            data: {
                labels: years,
                datasets: [
                    { label: 'Births / Naissances',    data: births,    backgroundColor: 'rgba(40,167,69,0.7)',  borderColor: '#28a745', borderWidth: 1 },
                    { label: 'Deaths / Décès',         data: deaths,    backgroundColor: 'rgba(220,53,69,0.7)', borderColor: '#dc3545', borderWidth: 1 },
                    { label: 'Marriages / Mariages',   data: marriages, backgroundColor: 'rgba(139,105,20,0.7)',borderColor: '#8B6914', borderWidth: 1 },
                    { label: 'Divorces',               data: divorces,  backgroundColor: 'rgba(91,44,111,0.7)', borderColor: '#5B2C6F', borderWidth: 1 },
                ]
            },
            options: {
                responsive: true,
                plugins: { legend: { position: 'bottom' } },
                scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
            }
        });
    }
});
