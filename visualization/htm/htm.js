var defaultjsonfile = 'icra.json';

loadhtm('');

function loadhtm(file)
{
  if (file == '') { file = defaultjsonfile;}
  else            { defaultjsonfile = file;};

  file = file.replace('C:\\fakepath\\', '');
  console.log('Loading file: '+file);

  var width  = 1270,
      height =  645;

  var i = 0,
      time  = 500,
      rectW = 140,
      rectH =  40;

  var tree = d3.layout.tree()
               .nodeSize([rectW+20, rectH])
               .separation(function separation(a, b) {
                  return (a.parent == b.parent ? 1 : 1.4);
                });

  var diagonal = d3.svg.diagonal()
                   .projection(function(d) { return [d.x+rectW/2, d.y+rectH/2]; });

  var svg = d3.select('svg')
              //responsive SVG needs these 2 attributes and no width and height attr
              .attr('preserveAspectRatio', 'xMinYMin meet')
              .attr('viewBox', '0 0 ' + width + ' ' + height)
              //class to make it responsive
              .classed('svg-content-responsive', true);

  svg.call(d3.behavior.zoom().scaleExtent([0.2, 5]).on('zoom', redraw));

  svg.append('text')
     .attr('dx', width/2)
     .attr('dy', height/15)
     .attr('class', 'title filename')
     .attr('text-anchor','middle')
     .text(file.replace('.json','').replace('_',' '));

  var vis  = svg.append('svg:g');

  var draw = vis.append('svg:g')
                .attr('transform', 'translate(' + (width-rectW)/2 + ',' + 100 + ')');

  // load the external data
  d3.json('json/'+file, function(error, json)
  {
    if (error) {throw error;}

    root = json.nodes;
    root.x0 = 0;
    root.y0 = 0;

    function collapse(d) {
        if (d.children) {
            d._children = d.children;
            d._children.forEach(collapse);
            d.children = null;
        }
    }

    // root.children.forEach(collapse);
    update(root);
  });

  function update(source) {
    // console.log(root);

    // Compute the new tree layout.
    var nodes = tree.nodes(root).reverse(),
        links = tree.links(nodes);

    // Normalize for fixed-depth.
    nodes.forEach(function(d) { d.y = d.depth * 100; });

    // Declare the nodes.
    var node = draw.selectAll('g.node')
                   .data(nodes, function(d) { return d.id; }); // { return d.id || (d.id = ++i); });

    // Enter the nodes.
    var nodeLabel = node.enter().append('g')
                        .attr('class', function(d) {
                          var res='node';
                          if (d.attributes) {res=res+' '+d.attributes.join(' ');}
                          if (d._children)  {res=res+' collapsed';}
                          return res;
                        })
                        .attr('transform', function(d) {
                          return 'translate(' + source.x0 + ',' + source.y0 + ')';
                        })
                        .on('click', click);

    var nodeRect = nodeLabel.append('rect')
                            .attr( 'width',   rectW)
                            .attr('height',   rectH)
                            .attr( 'class', 'label');

    var nodeText = nodeLabel.append('text')
                            .attr('x', rectW / 2)
                            .attr('y', rectH / 2)
                            .attr('dy', '.35em')
                            .attr('text-anchor', 'middle')
                            .text(function (d) { return d.name; });

    nodeRect.attr("width",  function(d) {
              d.rectWidth = this.nextSibling.getComputedTextLength() + 20;
              return d.rectWidth;
            })
            .attr("x",  function(d) {
              return (rectW - d.rectWidth)/2;
            })

    nodeText.attr('x', function(d) {
        return (rectW)/2;
    })

    // Add combination if there is a combination and the node is not collapsed
    nodeCombination = nodeLabel.filter(function(d){ return d.combination; })
                               .append('g')
                               .attr('class','combination');

    nodeCombination.append('rect')
                   .attr('width', 36)
                   .attr('height', 36)
                   .attr('x', function(d) {return (rectW-36)/2})
                   .attr('y', rectH + 1);

    nodeCombination.append('text')
                   .attr('x', function(d) {return (rectW)/2})
                   .attr('y', rectH / 2 - 12)
                   .attr('dy', '2.2em')
                   .attr('text-anchor', 'middle')
                   .text(function (d) {
                      if (d.combination==   'Parallel') {return '||';}
                      if (d.combination== 'Sequential') {return  '→';}
                      if (d.combination=='Alternative') {return  'v';}
                      return ''
                    });

    // Transition nodes to their new position.
    var nodeUpdate = node.transition()
                         .duration(time)
                         .attr('transform', function (d) {
                             return 'translate(' + d.x + ',' + d.y + ')';
                         });

    var gUpdate = nodeUpdate.attr('class', function(d) {
                                var cl=d3.select(this).attr('class');
                                if (d._children) {
                                    if (cl.indexOf(' collapsed')==-1) { return cl+' collapsed';}
                                }
                                else {
                                    if (cl.indexOf(' collapsed')!=-1) {
                                        return cl.replace(' collapsed','');
                                    }
                                }
                                return cl;
                            });


    // Transition exiting nodes to the parent's new position.
    var nodeExit = node.exit().transition()
                              .duration(time)
                              .attr('transform', function (d) {
                                  return 'translate(' + source.x + ',' + source.y + ')';
                              }).remove();

    // Declare the links...
    var link = draw.selectAll('path.link')
                   .data(links, function(d) { return d.target.id; });

    // console.log(link);
    // Enter any new links at the parent's previous position.
    link.enter().insert('path', 'g')
        .attr('class', 'link')
        .attr('x', rectW / 2)
        .attr('y', rectH / 2)
        .attr('d', function (d) {
          var o = {
              x: source.x0,
              y: source.y0
          };
          return diagonal({source: o, target: o});
        });

    // Transition links to their new position.
    link.transition()
        .duration(time)
        .attr('d', diagonal);

    // Transition exiting nodes to the parent's new position.
    link.exit().transition()
        .duration(time)
        .attr('d', function (d) {
          var o = {
              x: source.x,
              y: source.y
          };
          return diagonal({source: o, target: o});
        })
        .remove();

    // Stash the old positions for transition.
    nodes.forEach(function (d) {
        d.x0 = d.x;
        d.y0 = d.y;
    });

  };

  //Redraw for zoom
  function redraw() {
    vis.attr('transform',
             'translate(' + d3.event.translate + ')'
              + ' scale(' + d3.event.scale + ')');
  };


  function click(d) {
    console.log('Pressed item: '+d.name+'\tdepth: '+d.depth+'\tattr: '+d.attributes);
    // console.log(tree.links(d).toString());

    if (d.children) {
      d._children = d.children;
      d.children = null;
    } else {
      d.children = d._children;
      d._children = null;
    }
    update(d);
  }

};
